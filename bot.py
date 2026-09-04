import asyncio
import os
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.messages import RequestWebViewRequest, ImportChatInviteRequest
from telethon.errors import UserAlreadyParticipantError, FloodWaitError, InviteHashExpiredError
from playwright.async_api import async_playwright

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STRING = os.environ["SESSION_STRING"]
GROUP_INVITE = os.environ.get("GROUP_INVITE", "https://t.me/+HfoXTH_qx4k1Zjg0")
BUTTON_TEXT = os.environ.get("BUTTON_TEXT", "play lumberjack").lower()
MODE = os.environ.get("MODE", "recon")  # "recon" یا "play"
TARGET_SCORE = int(os.environ.get("TARGET_SCORE", "4000"))
VIEWPORT = {"width": 412, "height": 915}  # نسبت صفحه یک گوشی معمولی اندروید


async def join_group(client):
    """اول سعی می‌کنه عضو گروه بشه (اگه قبلاً عضو بوده، خطا رو نادیده می‌گیره)."""
    invite_hash = GROUP_INVITE.rstrip("/").split("/")[-1].lstrip("+")
    try:
        updates = await client(ImportChatInviteRequest(invite_hash))
        return updates.chats[0]
    except UserAlreadyParticipantError:
        return await client.get_entity(GROUP_INVITE)
    except InviteHashExpiredError:
        raise RuntimeError("لینک دعوت گروه منقضی/نامعتبره - یک لینک تازه بگیر")


async def get_game_url(client):
    entity = await join_group(client)
    print("عضو گروه شدیم / از قبل عضو بودیم:", getattr(entity, "title", entity))

    async for message in client.iter_messages(entity, limit=300):
        if not message.buttons:
            continue
        for row in message.buttons:
            for button in row:
                btn_text = (button.text or "").strip().lower()
                if BUTTON_TEXT in btn_text:
                    webview = await client(RequestWebViewRequest(
                        peer=entity,
                        bot=message.from_id if message.from_id else entity,
                        url=None,
                        platform="android",
                        reply_to=message.id,
                    ))
                    return webview.url
    raise RuntimeError("دکمه بازی پیدا نشد - BUTTON_TEXT یا GROUP_INVITE رو چک کن")


async def recon(url):
    """فقط HTML صفحه بازی رو توی لاگ چاپ می‌کنه تا selector درست رو پیدا کنیم."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page(viewport=VIEWPORT)
        await page.goto(url)
        await asyncio.sleep(4)

        html = await page.content()
        print("===== PAGE HTML START (before pressing play) =====")
        for i in range(0, len(html), 3000):
            print(html[i:i + 3000])
        print("===== PAGE HTML END =====")

        # سعی می‌کنیم دکمه پلی گرد وسط صفحه رو حدس بزنیم و کلیک کنیم
        # تا HTML صفحه بازی (با دکمه‌های چپ/راست) رو هم بگیریم
        try:
            await page.mouse.click(VIEWPORT["width"] / 2, VIEWPORT["height"] * 0.72)
            await asyncio.sleep(2)
            html2 = await page.content()
            print("===== PAGE HTML AFTER CLICK START =====")
            for i in range(0, len(html2), 3000):
                print(html2[i:i + 3000])
            print("===== PAGE HTML AFTER CLICK END =====")
        except Exception as e:
            print("کلیک روی دکمه پلی حدسی موفق نبود:", e)

        await browser.close()


async def play(url):
    """
    حالت بازی واقعی - فعلاً اسکلت‌ه.
    بعد از دیدن خروجی recon، selector های TODO رو کامل می‌کنیم.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page(viewport=VIEWPORT)
        await page.goto(url)
        await asyncio.sleep(3)

        # TODO: کلیک روی دکمه پلی داخل صفحه با selector واقعی
        # await page.click("SELECTOR_PLAY_BUTTON")
        await page.mouse.click(VIEWPORT["width"] / 2, VIEWPORT["height"] * 0.72)
        await asyncio.sleep(1.5)

        score = 0
        misses = 0
        while score < TARGET_SCORE and misses < 5:
            # TODO: خواندن سمت شاخه از DOM واقعی، مثلا:
            # side = await page.eval_on_selector(".branch.active", "el => el.dataset.side")
            side = None  # placeholder تا وقتی selector واقعی رو نداریم

            if side is None:
                misses += 1
                await asyncio.sleep(0.3)
                continue

            safe_side = "right" if side == "left" else "left"
            x = VIEWPORT["width"] * (0.25 if safe_side == "left" else 0.75)
            y = VIEWPORT["height"] * 0.92
            await page.mouse.click(x, y)
            await asyncio.sleep(0.3)

            # TODO: خواندن امتیاز واقعی از DOM
            # score = int(await page.eval_on_selector(".score", "el => el.textContent"))

        print(f"پایان - امتیاز نهایی: {score}")
        await browser.close()


async def main():
    try:
        async with TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH) as client:
            url = await get_game_url(client)
            print("Game URL:", url)

            if MODE == "recon":
                await recon(url)
            else:
                await play(url)
    except FloodWaitError as e:
        print(f"تلگرام گفته {e.seconds} ثانیه صبر کنیم. اسکریپت رو الان متوقف می‌کنیم "
              f"(دوباره خودتون دستی redeploy کنید بعد از اتمام این زمان).")
        return


if __name__ == "__main__":
    asyncio.run(main())
        
