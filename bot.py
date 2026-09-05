import asyncio
import io
import os
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.messages import RequestWebViewRequest, ImportChatInviteRequest, GetBotCallbackAnswerRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.functions.channels import GetParticipantsRequest
from telethon.tl.types import ChannelParticipantsBots, BotMenuButton, KeyboardButtonGame
from telethon.errors import UserAlreadyParticipantError, FloodWaitError, InviteHashExpiredError
from playwright.async_api import async_playwright
from PIL import Image

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STRING = os.environ["SESSION_STRING"]
GROUP_INVITE = os.environ.get("GROUP_INVITE", "https://t.me/+HfoXTH_qx4k1Zjg0")
BUTTON_TEXT = os.environ.get("BUTTON_TEXT", "play lumberjack").lower()
BOT_USERNAME = os.environ.get("BOT_USERNAME", "").lower()  # اختیاری: یوزرنیم دقیق بات بازی، مثلا gamebot
PINNED_MESSAGE_ID = int(os.environ.get("PINNED_MESSAGE_ID", "145397"))  # آیدی پیام پین شده با دکمه بازی
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", "4467330949"))  # آیدی عددی گروه (از لینک t.me/c/...)
MODE = os.environ.get("MODE", "recon")  # "recon" یا "play"
TARGET_SCORE = int(os.environ.get("TARGET_SCORE", "4000"))
VIEWPORT = {"width": 412, "height": 915}  # نسبت صفحه یک گوشی معمولی اندروید

# --- تنظیمات کالیبراسیون برای play() ---
LEFT_FRAC = float(os.environ.get("LEFT_FRAC", "0.32"))   # نسبت افقی نقطه چک چپ نسبت به عرض canvas
RIGHT_FRAC = float(os.environ.get("RIGHT_FRAC", "0.68"))  # نسبت افقی نقطه چک راست
ROW_FRAC = float(os.environ.get("ROW_FRAC", "0.78"))      # نسبت عمودی ردیف چک (نزدیک شخصیت)
SAMPLE_SIZE = int(os.environ.get("SAMPLE_SIZE", "16"))    # اندازه مربع نمونه‌برداری (پیکسل)
DIFF_THRESHOLD = float(os.environ.get("DIFF_THRESHOLD", "40"))  # آستانه تشخیص تغییر رنگ
TICK_INTERVAL = float(os.environ.get("TICK_INTERVAL", "0.15"))  # فاصله هر بررسی (ثانیه)
MAX_RUNTIME_SECONDS = int(os.environ.get("MAX_RUNTIME_SECONDS", "1200"))  # سقف زمانی کل اجرا


async def join_group(client):
    """
    اول بین دیالوگ‌های موجود (گروه‌هایی که از قبل عضوشونیم) می‌گرده -
    این هیچ درخواست حساس/فلود-محدودی به تلگرام نمی‌زنه.
    فقط اگه پیدا نشد (یعنی واقعاً عضو نیستیم)، از لینک دعوت جوین می‌شیم.
    """
    target_hash = GROUP_INVITE.rstrip("/").split("/")[-1].lstrip("+")

    async for dialog in client.iter_dialogs():
        if dialog.is_group or dialog.is_channel:
            if str(getattr(dialog.entity, "id", "")) == str(CHANNEL_ID):
                return dialog.entity

    print("توی دیالوگ‌های فعلی پیدا نشد - تلاش برای جوین از لینک دعوت")
    try:
        updates = await client(ImportChatInviteRequest(target_hash))
        return updates.chats[0]
    except UserAlreadyParticipantError:
        return await client.get_entity(GROUP_INVITE)
    except InviteHashExpiredError:
        raise RuntimeError("لینک دعوت گروه منقضی/نامعتبره - یک لینک تازه بگیر")


async def find_bots_in_group(client, entity):
    """همه بات‌های عضو گروه رو لیست می‌کنه."""
    try:
        result = await client(GetParticipantsRequest(
            entity, ChannelParticipantsBots(), offset=0, limit=100, hash=0
        ))
        return result.users
    except Exception as e:
        print("نتونستیم مستقیم لیست بات‌ها رو بگیریم:", e)
        return []


async def get_game_url_via_menu_button(client, entity):
    bots = await find_bots_in_group(client, entity)
    print(f"تعداد بات‌های پیدا شده در گروه: {len(bots)}")

    for bot in bots:
        uname = (bot.username or "").lower()
        print(f" - بات: @{bot.username} (id={bot.id})")
        if BOT_USERNAME and uname != BOT_USERNAME:
            continue

        full = await client(GetFullUserRequest(bot))
        bot_info = getattr(full.full_user, "bot_info", None)
        menu_button = getattr(bot_info, "menu_button", None) if bot_info else None

        if isinstance(menu_button, BotMenuButton):
            print(f"   menu_button پیدا شد: text={menu_button.text!r} url={menu_button.url!r}")
            webview = await client(RequestWebViewRequest(
                peer=entity,
                bot=bot,
                platform="android",
                from_bot_menu=True,
                url=menu_button.url,
            ))
            return webview.url
        else:
            print("   این بات menu_button از نوع WebApp نداره")

    return None


async def get_game_url_from_pinned(client, entity):
    """مستقیم پیامی که آیدیش رو می‌دونیم (پیام پین‌شده با دکمه شیشه‌ای) رو می‌خونه."""
    msg = await client.get_messages(entity, ids=PINNED_MESSAGE_ID)
    if not msg:
        print(f"پیام با آیدی {PINNED_MESSAGE_ID} پیدا نشد")
        return None
    if not msg.buttons:
        print(f"پیام {PINNED_MESSAGE_ID} دکمه‌ای نداره")
        return None

    for row in msg.buttons:
        for button in row:
            print(f"دکمه پیدا شد در پیام پین: text={button.text!r} type={type(button.button).__name__}")

            if isinstance(button.button, KeyboardButtonGame):
                answer = await client(GetBotCallbackAnswerRequest(
                    peer=entity,
                    msg_id=msg.id,
                    game=True,
                ))
                print(f"لینک بازی رسمی تلگرام گرفته شد: {answer.url}")
                return answer.url

            candidates = []
            via_bot = await msg.get_input_sender() if False else None
            if getattr(msg, "via_bot_id", None):
                try:
                    via_bot_entity = await client.get_entity(msg.via_bot_id)
                    candidates.append(("via_bot", via_bot_entity))
                except Exception as e:
                    print("نتونستیم via_bot رو resolve کنیم:", e)

            sender = await msg.get_sender()
            candidates.append(("sender", sender))

            for label, bot_candidate in candidates:
                try:
                    print(f"تلاش با {label}: {getattr(bot_candidate, 'username', bot_candidate)}")
                    webview = await client(RequestWebViewRequest(
                        peer=entity,
                        bot=bot_candidate,
                        url=None,
                        platform="android",
                    ))
                    return webview.url
                except Exception as e:
                    print(f"   شکست خورد با {label}: {e}")

            # اگه هیچ‌کدوم جواب نداد ولی خود دکمه یک URL مستقیم داره، همونو برگردون
            raw_url = getattr(button.button, "url", None)
            if raw_url:
                print(f"به‌جای WebView، از URL مستقیم دکمه استفاده می‌کنیم: {raw_url}")
                return raw_url

    return None


async def get_game_url(client):
    entity = await join_group(client)
    print("عضو گروه شدیم / از قبل عضو بودیم:", getattr(entity, "title", entity))

    # روش ۰: مستقیم از روی آیدی پیام پین‌شده (مطمئن‌ترین روش)
    url = await get_game_url_from_pinned(client, entity)
    if url:
        return url
    print("روش ۰ (پیام پین با آیدی مستقیم) جواب نداد - می‌ریم سراغ روش ۱")

    # روش ۱: دکمه شیشه‌ای زیر یک پیام
    async for message in client.iter_messages(entity, limit=300):
        if not message.buttons:
            continue
        for row in message.buttons:
            for button in row:
                btn_text = (button.text or "").strip().lower()
                if BUTTON_TEXT in btn_text:
                    sender = await message.get_sender()
                    webview = await client(RequestWebViewRequest(
                        peer=entity,
                        bot=sender,
                        url=None,
                        platform="android",
                    ))
                    return webview.url
    print("روش ۱ (پیام) جواب نداد - می‌ریم سراغ روش ۲ (منوی بات)")

    # روش ۲: Menu Button وب‌اپ بات
    url = await get_game_url_via_menu_button(client, entity)
    if url:
        return url

    raise RuntimeError("دکمه بازی پیدا نشد - BUTTON_TEXT یا GROUP_INVITE رو چک کن")


async def recon(url):
    """به‌جای گرفتن اسکرین‌شات سنگین، مستقیم رنگ چند پیکسل کلیدی روی canvas رو
    از طریق جاوااسکریپت می‌خونه (خیلی سبک‌تر، به‌صورت عدد توی لاگ) درحالی‌که
    واقعاً بازی می‌کنه (کلیک متناوب چپ/راست) تا داده کافی برای کالیبراسیون بگیریم."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page(viewport=VIEWPORT)
        await page.goto(url)
        await asyncio.sleep(3)

        canvas = page.locator("#canvas_wrap canvas")
        box = await canvas.bounding_box()
        print(f"canvas bounding box: {box}")

        # شروع بازی: کلیک روی دکمه پلی که خودش داخل canvas رسم شده
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] * 0.85
        await page.mouse.click(cx, cy)
        await asyncio.sleep(0.8)

        captured = False
        for tick in range(30):
            try:
                pw_class = await page.eval_on_selector(
                    "body", "el => (document.getElementById('page_wrap')||{}).className || ''"
                )
            except Exception:
                pw_class = "?"

            print(f"t={tick} class={pw_class}")

            if (not captured) and pw_class and "in_game" in pw_class:
                captured = True
                png_bytes = await canvas.screenshot()
                b64 = __import__("base64").b64encode(png_bytes).decode()
                print(f"===== IN_GAME SCREENSHOT BASE64 START (len={len(b64)}) =====")
                for i in range(0, len(b64), 2000):
                    print(f"B64|{i}|{b64[i:i+2000]}")
                print("===== IN_GAME SCREENSHOT BASE64 END =====")

            # کلیک متناوب چپ/راست فقط برای اینکه بازی چند فریم بیشتر زنده بمونه
            side = "left" if tick % 2 == 0 else "right"
            try:
                await page.click(f"#button_{side}", timeout=500)
            except Exception:
                pass

            await asyncio.sleep(0.25)

            if pw_class and "in_result" in pw_class and tick > 2:
                print(f"t={tick} بازی تموم شد (برگشت به نتیجه)، دوباره شروع می‌کنیم")
                try:
                    await page.mouse.click(cx, cy)
                    await asyncio.sleep(0.8)
                except Exception:
                    pass

            if captured and tick > 8:
                break

        await browser.close()


async def sample_avg_color(page, box):
    """میانگین رنگ یک ناحیه کوچیک از صفحه رو برمی‌گردونه (با اسکرین‌شات واقعی، نه canvas API)."""
    png_bytes = await page.screenshot(clip=box)
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    pixels = list(img.getdata())
    n = len(pixels)
    r = sum(p[0] for p in pixels) / n
    g = sum(p[1] for p in pixels) / n
    b = sum(p[2] for p in pixels) / n
    return (r, g, b)


def color_diff(c1, c2):
    return sum(abs(a - b) for a, b in zip(c1, c2))


async def play(url):
    """
    حالت بازی واقعی: چون canvas به‌خاطر CORS تصاویر cross-origin "آلوده" شده،
    getImageData جواب نمی‌ده. به‌جاش با اسکرین‌شات واقعی Playwright (که این
    محدودیت رو نداره) دو نقطه کنار تنه درخت (چپ/راست) رو در لحظه‌ی امن شروع
    به‌عنوان "رنگ خالی" ذخیره می‌کنیم؛ هر تیک بعدی، هرکدوم که رنگش عوض شده
    باشه یعنی شاخه اونجاست، پس سمت مقابل رو می‌زنیم.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page(viewport=VIEWPORT)
        await page.goto(url)
        await asyncio.sleep(3)

        canvas = page.locator("#canvas_wrap canvas")
        canvas_box = await canvas.bounding_box()

        # نقاط نمونه‌برداری: کمی چپ و کمی راست تنه، در ارتفاعی نزدیک شخصیت
        left_x = canvas_box["x"] + canvas_box["width"] * LEFT_FRAC
        right_x = canvas_box["x"] + canvas_box["width"] * RIGHT_FRAC
        sample_y = canvas_box["y"] + canvas_box["height"] * ROW_FRAC

        def region(cx):
            return {
                "x": cx - SAMPLE_SIZE / 2,
                "y": sample_y - SAMPLE_SIZE / 2,
                "width": SAMPLE_SIZE,
                "height": SAMPLE_SIZE,
            }

        total_score = 0
        rounds = 0
        start_time = asyncio.get_event_loop().time()

        async def get_class():
            try:
                return await page.eval_on_selector(
                    "body", "el => (document.getElementById('page_wrap')||{}).className || ''"
                )
            except Exception:
                return ""

        async def start_round():
            cx = canvas_box["x"] + canvas_box["width"] / 2
            cy = canvas_box["y"] + canvas_box["height"] * 0.85
            for attempt in range(6):
                await page.mouse.click(cx, cy)
                await asyncio.sleep(0.5)
                cls = await get_class()
                if "in_game" in (cls or ""):
                    return True
                await asyncio.sleep(0.3)
            print("هشدار: بعد از چند تلاش وارد حالت in_game نشدیم؛ class فعلی:", await get_class())
            return False

        await start_round()
        baseline_left = await sample_avg_color(page, region(left_x))
        baseline_right = await sample_avg_color(page, region(right_x))
        print(f"baseline: left={baseline_left} right={baseline_right}")

        current_side = "left"  # فرض اولیه‌ی سمت ایستادن شخصیت؛ اگه برعکس بود با LEFT_FRAC/RIGHT_FRAC جابجا کن
        consecutive_zero = 0

        while total_score < TARGET_SCORE:
            if asyncio.get_event_loop().time() - start_time > MAX_RUNTIME_SECONDS:
                print("زمان مجاز اجرا تموم شد، خارج می‌شیم")
                break

            try:
                pw_class = await page.eval_on_selector(
                    "body", "el => (document.getElementById('page_wrap')||{}).className || ''"
                )
            except Exception:
                pw_class = ""

            if "in_result" in (pw_class or ""):
                try:
                    score_text = await page.eval_on_selector(
                        "#score_value", "el => el.textContent"
                    )
                    round_score = int("".join(ch for ch in score_text if ch.isdigit()) or 0)
                except Exception:
                    round_score = 0
                total_score += round_score
                rounds += 1
                consecutive_zero = consecutive_zero + 1 if round_score == 0 else 0
                print(f"دور {rounds} تموم شد - امتیاز این دور: {round_score} - مجموع: {total_score}")
                if consecutive_zero >= 15:
                    print("۱۵ دور پشت سر هم امتیاز صفر بود - کالیبراسیون نیاز به بازبینی داره، خارج می‌شیم")
                    break
                await start_round()
                baseline_left = await sample_avg_color(page, region(left_x))
                baseline_right = await sample_avg_color(page, region(right_x))
                current_side = "left"
                continue

            left_color = await sample_avg_color(page, region(left_x))
            right_color = await sample_avg_color(page, region(right_x))

            diff_left = color_diff(left_color, baseline_left)
            diff_right = color_diff(right_color, baseline_right)

            danger_side = None
            if diff_left > DIFF_THRESHOLD and diff_left >= diff_right:
                danger_side = "left"
            elif diff_right > DIFF_THRESHOLD and diff_right > diff_left:
                danger_side = "right"

            if danger_side is not None and danger_side == current_side:
                safe_side = "right" if danger_side == "left" else "left"
                try:
                    await page.click(f"#button_{safe_side}", timeout=500)
                    current_side = safe_side
                except Exception as e:
                    print("خطا در کلیک:", e)

            await asyncio.sleep(TICK_INTERVAL)

        print(f"پایان - مجموع امتیاز: {total_score} در {rounds} دور")
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
