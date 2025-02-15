from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import StreamingResponse
import os
import json
import requests
import subprocess
import glob
import cv2
from zipfile import ZipFile
from dotenv import load_dotenv
from multiprocessing import Pool

# Load environment variables from .env file
load_dotenv()

app = FastAPI()

# Configuration
json_file = "Art_images.json"
download_folder = "./midjourney_images"
framed_folder = "./framed_images"
downloaded_log = "./downloaded_images.log"
zip_output = "./framed_images.zip"

# Securely load Discord credentials from .env file
discord_exporter_path = "./DiscordChatExporter.CLI"
discord_token = os.getenv("DISCORD_TOKEN")
channel_id = os.getenv("DISCORD_CHANNEL_ID")

# Ensure folders exist
os.makedirs(download_folder, exist_ok=True)
os.makedirs(framed_folder, exist_ok=True)

# Ensure log file exists
if not os.path.exists(downloaded_log):
    open(downloaded_log, "w").close()


def load_downloaded_images(force_download=False):
    """ Load already downloaded images unless force_download is enabled """
    if force_download:
        return set()
    if os.path.exists(downloaded_log):
        with open(downloaded_log, "r") as f:
            return set(f.read().splitlines())
    return set()


def hex_to_bgr(hex_color):
    """ Convert HEX color to BGR format for OpenCV """
    hex_color = hex_color.lstrip("#")
    rgb = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    return (rgb[2], rgb[1], rgb[0])  # Convert RGB to BGR


def apply_frame(params):
    """ Applies a frame to the given image file """
    filename, frame_size, frame_color = params
    input_path = os.path.join(download_folder, filename)
    output_path = os.path.join(framed_folder, filename)
    
    img = cv2.imread(input_path)
    if img is None:
        return f"⚠️ Skipping {filename} (invalid image)"
    
    border_color = hex_to_bgr(frame_color)
    
    img_with_border = cv2.copyMakeBorder(
        img, frame_size, frame_size, frame_size, frame_size,
        cv2.BORDER_CONSTANT, value=border_color
    )
    cv2.imwrite(output_path, img_with_border)
    return f"✅ Framed: {filename}"


@app.get("/")
def home():
    return {"message": "Artcha Automation API is running"}


@app.get("/run")
def run_automation(
    frame_color: str = Query(default="000000"), 
    frame_size: int = Query(default=30), 
    force_download: bool = Query(default=False)
):
    """ Runs the full automation and streams logs in real time """

    if not discord_token or not channel_id:
        return HTTPException(status_code=500, detail="Missing Discord credentials.")

    downloaded_images = load_downloaded_images(force_download)
    new_downloads = []
    new_images = 0  # ✅ Initialize new_images properly

    def event_stream():
        nonlocal new_images  # ✅ Fix scope issue

        yield "🚀 Starting automation...\n"
        yield "📤 Exporting Discord chat...\n"

        # Step 1: Run DiscordChatExporter
        export_command = [discord_exporter_path, "export", "-t", discord_token, "-c", channel_id, "-f", "Json"]
        subprocess.run(export_command)
        yield "✅ Discord export complete.\n"

        # Step 2: Rename JSON file
        json_files = glob.glob("*art_channel*.json")
        if json_files:
            os.rename(json_files[0], json_file)
            yield f"✅ Found JSON file: {json_files[0]}\n"
        else:
            yield "⚠️ JSON file not found. Aborting.\n"
            return

        # Step 3: Download new images
        try:
            with open(json_file, "r", encoding="utf-8") as file:
                data = json.load(file)
        except Exception as e:
            yield f"⚠️ Error reading JSON: {e}\n"
            return

        total_images = len(data.get("messages", []))
        downloaded_count = 0

        for message in data.get("messages", []):
            for attachment in message.get("attachments", []):
                url, filename = attachment.get("url", ""), attachment.get("fileName", "")

                if not url or filename in downloaded_images:
                    continue  

                file_path = os.path.join(download_folder, filename)
                try:
                    response = requests.get(url, stream=True)
                    if response.status_code == 200:
                        with open(file_path, "wb") as img_file:
                            for chunk in response.iter_content(1024):
                                img_file.write(chunk)
                        new_downloads.append(filename)
                        new_images += 1
                        downloaded_count += 1
                        yield f"⬇️ Downloading {downloaded_count}/{total_images}: {filename}\n"
                    else:
                        yield f"⚠️ Failed to download {url}: HTTP {response.status_code}\n"
                except Exception as e:
                    yield f"⚠️ Error downloading {url}: {e}\n"

        if new_downloads:
            with open(downloaded_log, "a") as log_file:
                log_file.write("\n".join(new_downloads) + "\n")

        # Step 4: Apply frames to new images
        framed_images = 0
        pool_args = [(filename, frame_size, frame_color) for filename in new_downloads]

        if new_images > 0:
            yield "🖼️ Applying frames to images...\n"
            with Pool() as pool:
                results = pool.map(apply_frame, pool_args)

            for idx, result in enumerate(results):
                yield f"🎨 Framing {idx+1}/{new_images}: {result}\n"
                if "✅ Framed" in result:
                    framed_images += 1

        # Step 5: Create ZIP file
        if framed_images > 0:
            with ZipFile(zip_output, 'w') as zipf:
                for file in os.listdir(framed_folder):
                    zipf.write(os.path.join(framed_folder, file), file)
                    yield f"📦 Added to ZIP: {file}\n"

            yield f"✅ ZIP file created: {zip_output}\n"
        else:
            yield "⚠️ No images were framed, skipping ZIP creation.\n"

        yield f"✅ Processed {new_images} new images, framed {framed_images}. Download from /download\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/download")
def download_zip():
    """ Allows the user to download the ZIP file of framed images """
    if os.path.exists(zip_output):
        return {"download_url": zip_output}
    return {"error": "No ZIP file found"}
