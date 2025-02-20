import os
import shutil
import asyncio
import requests
from pyrogram import Client, filters
from pyrogram.types import Message
import img2pdf
import json
import re
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, UnidentifiedImageError
from fpdf import FPDF

# Bot configurations
API_ID = "24250238"
API_HASH = "cb3f118ce5553dc140127647edcf3720"
BOT_TOKEN = "6687465225:AAHF-yb3LNKeFZzJUQEZIj9zgpp6k-o56qo"

# Initialize the bot
app = Client("book_downloader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Store ongoing tasks
user_tasks = {}

# ThreadPoolExecutor for parallel downloads
executor = ThreadPoolExecutor(max_workers=5)

# Cookie configuration
CI_DATABASE = os.getenv("CI_DATABASE", "286dbaf9a7ca6c62546cddfac56833b3860f5c53")
CI_SESSION = os.getenv("CI_SESSION", "880b1fcdd0d4b9e6cc88f979e217e3136184665b")

def get_cookies():
    return f"ci_database={CI_DATABASE}; ci_session={CI_SESSION}"

@app.on_message(filters.command("start"))
async def start_command(client, message: Message):
    await message.reply("Welcome to the Book Downloader Bot!\nSend /download to start downloading a book.")

@app.on_message(filters.command("cookie"))
async def update_cookies(client, message: Message):
    global CI_DATABASE, CI_SESSION
    try:
        args = message.text.split()
        if len(args) != 3:
            await message.reply("Invalid command format! Use:\n`/cookie <ci_database> <ci_session>`", parse_mode="markdown")
            return
        CI_DATABASE = args[1]
        CI_SESSION = args[2]
        await message.reply("Cookies updated successfully!")
    except Exception as e:
        await message.reply(f"An error occurred while updating cookies: {e}")

@app.on_message(filters.command("download"))
async def download_command(client, message: Message):
    user_id = message.from_user.id
    if user_id in user_tasks:
        await message.reply("You already have an ongoing task. Please wait or send /cancel to stop it.")
        return
    user_tasks[user_id] = {"status": "awaiting_book_id"}
    await message.reply("Please send the book ID to start downloading.")

@app.on_message(filters.command("cancel"))
async def cancel_command(client, message: Message):
    user_id = message.from_user.id
    if user_id not in user_tasks:
        await message.reply("You don't have any ongoing tasks.")
        return
    user_folder = f"downloads/{user_id}/"
    shutil.rmtree(user_folder, ignore_errors=True)
    user_tasks.pop(user_id, None)
    await message.reply("Your task has been canceled and all temporary data has been deleted.")

@app.on_message(filters.text)
async def handle_book_id(client, message: Message):
    user_id = message.from_user.id
    if user_id not in user_tasks:
        return

    user_task = user_tasks[user_id]
    if user_task["status"] == "awaiting_book_id":
        book_id = message.text.strip()
        user_task["book_id"] = book_id
        user_task["status"] = "downloading"
        status = await message.reply("Got it! Fetching book details...")
        await download_book(client, status, message, user_task)

def download_page(page: int, book_id: str, user_folder: str):
    page_url = f"https://yctpublication.com/getPage/{book_id}/{page}"
    output_file = f"{user_folder}{page:03d}.jpg"  # Using 3-digit padding for correct ordering
    
    headers = {
        "accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9",
        "cookie": get_cookies(),
        "referer": f"https://yctpublication.com/readbook/{book_id}",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    }

    try:
        response = requests.get(page_url, headers=headers)
        if response.status_code == 200:
            with open(output_file, "wb") as f:
                f.write(response.content)
            return True
    except Exception as e:
        print(f"Error downloading page {page}: {e}")
    return False

async def create_pdf_from_images(image_paths, output_pdf_path):
    try:
        # Sort image paths to ensure correct page order
        image_paths.sort()
        
        # Filter valid images and convert to RGB if needed
        valid_images = []
        for img_path in image_paths:
            try:
                with Image.open(img_path) as img:
                    if img.mode != 'RGB':
                        rgb_img = img.convert('RGB')
                        rgb_img.save(img_path)
                    valid_images.append(img_path)
            except Exception as e:
                print(f"Error processing image {img_path}: {e}")
                continue

        if valid_images:
            # Use img2pdf for better quality
            with open(output_pdf_path, "wb") as f:
                f.write(img2pdf.convert(valid_images))
            return True
        return False
    except Exception as e:
        print(f"Error creating PDF: {e}")
        return False

async def download_book(client, status, message: Message, user_task: dict):
    user_id = message.from_user.id
    book_id = user_task["book_id"]
    user_folder = f"downloads/{user_id}/"
    os.makedirs(user_folder, exist_ok=True)

    try:
        # Get book details
        response = requests.get(f"https://yctpublication.com/master/api/MasterController/bookdetails?bookid={book_id}")
        if response.status_code != 200:
            raise Exception("Failed to fetch book details")

        book_details = response.json() if "application/json" in response.headers.get("Content-Type", "").lower() else json.loads(re.search(r'({.*})', response.text).group(0))
        
        if not book_details.get("status"):
            raise Exception(f"API error: {book_details.get('message', 'Unknown error')}")

        book_name = book_details["data"].get("book_name", "Unknown_Book").replace(" ", "_")
        no_of_pages = int(book_details["data"].get("no_of_pages", 0))

        if no_of_pages == 0:
            raise Exception("Invalid number of pages")

        await status.edit(f"📚 Downloading: {book_name}\n📄 Pages: {no_of_pages}")

        # Download pages in parallel
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = []
            for page in range(1, no_of_pages + 1):
                future = loop.run_in_executor(executor, download_page, page, book_id, user_folder)
                futures.append(future)
            await asyncio.gather(*futures)

        await status.edit("📑 Creating PDF...")

        # Create PDF
        pdf_path = f"{user_folder}{book_name}.pdf"
        image_paths = [f"{user_folder}{i:03d}.jpg" for i in range(1, no_of_pages + 1)]
        
        if await create_pdf_from_images(image_paths, pdf_path):
            await status.edit("📤 Uploading PDF...")
            await client.send_document(
                chat_id=user_id,
                document=pdf_path,
                caption=f"📚 {book_name}\n📄 {no_of_pages} pages"
            )
            await status.delete()
        else:
            raise Exception("Failed to create PDF")

    except Exception as e:
        await message.reply(f"❌ Error: {str(e)}")
    finally:
        # Cleanup
        shutil.rmtree(user_folder, ignore_errors=True)
        user_tasks.pop(user_id, None)

app.run()
