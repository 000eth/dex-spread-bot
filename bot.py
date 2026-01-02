import asyncio
import aiohttp
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv
import os

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "PASTE_YOUR_TOKEN_HERE")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

class Settings(StatesGroup):
    profit = State()

user_settings = {}

DEXS = {
    'extended': {
        'url': 'https://api.starknet.extended.exchange/api/v1/info/markets/BTC-USD/stats',
        'price_path': lambda data: float(data['data']['markPrice'])
    },
    'nado': {
        'url': 'https://gateway.nado.xyz/api/v1/markets/BTC-USD/stats',
        'price_path': lambda data: float(data['data']['markPrice'])
    }
}

FEES = {'extended': 0.00025, 'nado': 0.00035}
NOTIONAL = 10000
LEVERAGE = 10
MIN_PROFIT = 1

logging.basicConfig(level=logging.INFO)

async def fetch_all_prices():
    """Параллельный запрос цен"""
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_price(session, dex, config) for dex, config in DEXS.items()]
        return await asyncio.gather(*tasks, return_exceptions=True)

async def fetch_price(session, dex, config):
    try:
        async with session.get(config['url'], timeout=10) as resp:
            if resp.status != 200:
                logging.error(f"{dex} HTTP {resp.status}")
                return dex, None
            data = await resp.json()
            price = config['price_path'](data)
            logging.info(f"{dex}: ${price:,.0f}")
            return dex, price
    except Exception as e:
        logging.error(f"{dex} error: {e}")
        return dex, None

def calc_spread(p1, p2, dex1, dex2):
    if not p1 or not p2: return None
    if p1 > p2:
        p1, p2 = p2, p1
        dex1, dex2 = dex2, dex1
    units = NOTIONAL / p1
    gross = units * (p2 - p1)
    fees = 2 * FEES[dex1] * NOTIONAL + 2 * FEES[dex2] * NOTIONAL
    net = gross - fees
    gross_pct = (p2 - p1) / p1 * 100
    return {
        'cheap': (dex1, p1), 
        'expensive': (dex2, p2), 
        'gross': gross,
        'gross_pct': gross_pct,
        'fees': fees, 
        'net': net
    }

@dp.message(Command("start"))
async def start_handler(msg: Message):
    await msg.answer("""
🚀 DexSpread Bot v1.0 ✅

📊 BTC: Extended + Nado ($10k/10x)

Команды:
/scan     ← спреды + цены
/settings ← min profit ($1/$10/$30/$100)
/status   ← настройки

#arbitrage #perp #DEX
    """)

@dp.message(Command("status"))
async def status_handler(msg: Message):
    user_id = msg.from_user.id
    threshold = user_settings.get(user_id, {}).get('min_profit', MIN_PROFIT)
    await msg.answer(
        f"⚙️ Статус\n"
        f"Min profit: **${threshold}**\n"
        f"Notional: **${NOTIONAL:,}**\n"
        f"Leverage: **{LEVERAGE}x**\n"
        f"Биржи: Extended, Nado\n\n/scan",
        parse_mode="Markdown"
    )

@dp.message(Command("scan"))
async def scan_handler(msg: Message):
    user_id = msg.from_user.id
    threshold = user_settings.get(user_id, {}).get('min_profit', MIN_PROFIT)
    
    await msg.answer("🔄 Сканирую цены...")
    
    prices = await fetch_all_prices()
    price_dict = {dex: price for dex, price in prices if price is not None}
    
    # Показываем ВСЕ цены
    prices_text = "\n".join([f"  {dex.upper()}: ${p:,.0f}" for dex, p in price_dict.items()])
    
    spreads = []
    for d1 in price_dict:
        for d2 in price_dict:
            if d1 != d2:
                spread = calc_spread(price_dict[d1], price_dict[d2], d1, d2)
                if spread and spread['net'] >= threshold:
                    spreads.append(spread)
    
    if spreads:
        best = max(spreads, key=lambda x: x['net'])
        cheap_dex, cheap_p = best['cheap']
        exp_dex, exp_p = best['expensive']
        deal_id = len(spreads)
        
        await msg.answer(f"""
🚨 BTC #{deal_id}: {cheap_dex.upper()} ${cheap_p:,.0f} 
↔ {exp_dex.upper()} ${exp_p:,.0f}

📊 ${NOTIONAL:,} | {LEVERAGE}x

💰 Gross: ${best['gross']:,.0f} ({best['gross_pct']:.2f}%)
💸 Fees:  ${best['fees']:,.0f}
✅ **Net: ${best['net']:,.0f} PROFIT**

#open #{deal_id} #BTC #{cheap_dex}{exp_dex}
        """, parse_mode="Markdown")
    else:
        await msg.answer(f"""
📊 Цены:
{prices_text}

💤 Спредов < **${threshold}**
        """, parse_mode="Markdown")

@dp.message(Command("settings"))
async def settings_handler(msg: Message):
    user_id = msg.from_user.id
    current = user_settings.get(user_id, {}).get('min_profit', MIN_PROFIT)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="$1", callback_data="profit_1"),
            InlineKeyboardButton(text="$10", callback_data="profit_10")
        ],
        [
            InlineKeyboardButton(text="$30", callback_data="profit_30"),
            InlineKeyboardButton(text="$100", callback_data="profit_100")
        ]
    ])
    
    await msg.answer(
        f"⚙️ Min profit: **${current}**\n"
        f"Выбери threshold:",
        reply_markup=kb, parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("profit_"))
async def profit_callback(callback):
    profit = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    user_settings[user_id] = user_settings.get(user_id, {})
    user_settings[user_id]['min_profit'] = profit
    
    await callback.message.edit_text(
        f"✅ **Min profit: ${profit}**\n"
        f"📊 ${NOTIONAL:,} | {LEVERAGE}x\n\n"
        f"/scan для теста",
        parse_mode="Markdown"
    )
    await callback.answer()

async def main():
    print("🚀 Bot starting...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

