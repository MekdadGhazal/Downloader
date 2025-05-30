import os
import sys
import csv
import time
import json
import shutil
import yt_dlp
import logging
import tempfile # Imported but not actively used, keep for potential future use
import requests
# import subprocess # Not used
import instaloader
# from tqdm import tqdm # Not used in bot context
# from pyfiglet import Figlet # Not used
# from termcolor import colored # Not used in bot messages
from datetime import datetime
# from tabulate import tabulate # Not used in bot messages
# from concurrent.futures import ThreadPoolExecutor # Not used

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler
from urllib.parse import urlparse

from dotenv import load_dotenv

# --- Basic Logging Configuration ---
# Configure logging once at the beginning
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

load_dotenv()
IG_USERNAME = os.getenv("IG_USERNAME")
IG_PASSWORD = os.getenv("IG_PASSWORD")
BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_OWNER_ID_STR = os.getenv("BOT_OWNER_ID")

BOT_OWNER_ID = None
if BOT_OWNER_ID_STR:
    try:
        BOT_OWNER_ID = int(BOT_OWNER_ID_STR)
    except ValueError:
        logger.error(f"Invalid BOT_OWNER_ID: '{BOT_OWNER_ID_STR}'. Must be an integer.")
        # Decide on behavior: exit, or run without owner notifications
        # For now, it will just log an error if owner notifications fail

if not BOT_TOKEN:
    logger.critical("BOT_TOKEN environment variable not found. Exiting.")
    sys.exit(1)

# This global list seems intended for a broader URL validation,
# but the bot primarily uses detect_platform.
# yt_dlp supports many of these directly if detect_platform identifies them broadly.
# The googleusercontent.com entries are highly specific and likely not needed if yt_dlp handles YouTube URLs.
allowed_domains_general = [
    "youtube.com", "youtu.be", # Simplified YouTube
    "tiktok.com",
    "facebook.com", "fb.watch",
    "x.com", "twitter.com",
    "twitch.tv", "clips.twitch.tv",
    "snapchat.com",
    "reddit.com", "v.redd.it", "i.redd.it", "packaged-media.redd.it",
    "vimeo.com",
    "streamable.com",
    "pinterest.com", "pin.it",
    "linkedin.com",
    "bilibili.tv", "bilibili.com",
    "odysee.com",
    "rumble.com",
    "gameclips.io",
    "triller.co",
    "snackvideo.com",
    "kwai.com",
    "instagram.com",
    "threads.net", # Added based on detect_platform
]

# For storing user-specific format choices temporarily
user_format_data = {}


# ---------------------------------
# Utility Functions
# ---------------------------------
def check_internet_connection():
    """Check if the system has an active internet connection."""
    try:
        requests.head("https://www.google.com", timeout=5)
        return True
    except requests.ConnectionError:
        logger.warning("No internet connection detected.")
        return False

# Not directly used by bot handlers, more for CLI startup
def ensure_internet_connection_cli():
    """Ensure that an internet connection is active before proceeding (CLI)."""
    while not check_internet_connection():
        print("\033[91m\nNo internet connection. Retrying in 5 seconds...\033[0m")
        time.sleep(5)
    print("\033[92mInternet connection detected. Proceeding...\033[0m")


# -------------------------------------
# Validate URLs for Supported Platforms (General Validator)
# -------------------------------------
def is_valid_general_url(url, domains_list):
    """Check if the URL matches one of the allowed domains."""
    # Basic check, might need refinement for subdomains etc.
    parsed_url = urlparse(url)
    netloc = parsed_url.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return any(domain_item == netloc for domain_item in domains_list)


# ---------------------------------
# Download Functions for Instagram
# ---------------------------------
def download_instagram_post(url: str, base_download_path: str, user_identifier: str):
    """
    Downloads Instagram post (images/videos).
    Creates a directory structure: base_download_path/user_identifier/post_shortcode/
    Returns a list of file paths for downloaded media or None on failure.
    Cleans up the post_shortcode directory after successful processing if files are sent.
    """
    ig_allowed_domains = ["instagram.com", "www.instagram.com"]
    parsed_url_netloc = urlparse(url).netloc.lower()
    if not any(domain in parsed_url_netloc for domain in ig_allowed_domains):
        logger.error(f"Invalid Instagram URL: {url}")
        return None

    if not IG_USERNAME or not IG_PASSWORD:
        logger.error("Instagram username or password not configured.")
        return None

    if not check_internet_connection():
        return None # Bot should handle informing user

    downloaded_files = []
    post_specific_dir = "" # Initialize for finally block

    try:
        L = instaloader.Instaloader(
            download_pictures=True,
            download_videos=True,
            download_video_thumbnails=False, # Usually not needed
            download_geotags=False,
            download_comments=False,
            save_metadata=False, # Don't save metadata JSON files
            compress_json=False,
            post_metadata_txt_pattern="", # No txt files
            filename_pattern="{profile}_{shortcode}_{medianame}", # Customizable
        )

        try:
            # Try loading session, then login. Session file named after username.
            L.load_session_from_file(IG_USERNAME)
            logger.info(f"Instagram session loaded successfully for {IG_USERNAME}.")
        except FileNotFoundError:
            logger.info(f"No session file found for {IG_USERNAME}. Attempting login...")
            try:
                L.login(IG_USERNAME, IG_PASSWORD)
                L.save_session_to_file()
                logger.info(f"Instagram login successful for {IG_USERNAME} and session saved.")
            except instaloader.exceptions.ConnectionException as e:
                logger.error(f"Instagram login failed for {IG_USERNAME}: {e}")
                return None # Cannot proceed without login
        except instaloader.exceptions.ConnectionException as e: # Catch errors during session load too
             logger.error(f"Failed to use loaded Instagram session for {IG_USERNAME}: {e}")
             return None


        # Extract shortcode: last part of the path, ignoring empty parts and query params
        path_parts = urlparse(url).path.strip('/').split('/')
        shortcode = ""
        if "p" in path_parts:
            shortcode_index = path_parts.index("p") + 1
            if shortcode_index < len(path_parts):
                shortcode = path_parts[shortcode_index]
        elif "reel" in path_parts:
            shortcode_index = path_parts.index("reel") + 1
            if shortcode_index < len(path_parts):
                shortcode = path_parts[shortcode_index]
        elif "reels" in path_parts: # For /reels/shortcode/ format
            shortcode_index = path_parts.index("reels") + 1
            if shortcode_index < len(path_parts):
                shortcode = path_parts[shortcode_index]
        
        if not shortcode:
            logger.error(f"Could not extract shortcode from Instagram URL: {url}")
            return None

        post = instaloader.Post.from_shortcode(L.context, shortcode)

        user_dir = os.path.join(base_download_path, user_identifier)
        post_specific_dir = os.path.join(user_dir, shortcode) # This will be the target for instaloader
        os.makedirs(post_specific_dir, exist_ok=True)
        
        # Set Instaloader's download target for this post
        L.dirname_pattern = post_specific_dir 
        # We need to reset filename_pattern if we want files directly in post_specific_dir
        # without the {profile}_{shortcode} prefix again if dirname_pattern already includes it.
        # A simpler way might be to download_post to a temp name and then collect.
        # Or use L.download_pic / L.download_video if we iterate nodes.

        logger.info(f"Downloading Instagram post {shortcode} to {post_specific_dir}")

        # Instaloader's download_post will download all media and potentially metadata.
        # We will filter for .jpg and .mp4 files based on its output structure.
        # For simplicity, let download_post handle it, then collect.
        L.download_post(post, target=shortcode) # target here is relative to cwd or instaloader's context
                                                # this might be problematic.
                                                # Let's ensure it downloads into post_specific_dir
        # Corrected approach for instaloader target:
        # L.download_post already uses dirname_pattern if set, target arg in download_post is a prefix.
        # To ensure files land in `post_specific_dir` and are identifiable:
        
        # Clear dirname_pattern to avoid nested dirs if target in download_post is complex
        # L.dirname_pattern = "" # This might be too disruptive.
        # The default behavior is that download_post creates a subdir named by `target` argument or post owner
        # Let's try to make Instaloader download directly into post_specific_dir without further subdirs
        
        # Option 1: Iterate and download specific URLs (more control)
        if post.typename == 'GraphSidecar': # Album
            for i, node in enumerate(post.get_sidecar_nodes()):
                file_ext = "jpg" if not node.is_video else "mp4"
                temp_filename = f"media_{i+1}.{file_ext}"
                file_path = os.path.join(post_specific_dir, temp_filename)
                if node.is_video:
                    L.download_url(node.video_url, filename=file_path)
                else:
                    L.download_url(node.display_url, filename=file_path)
                if os.path.exists(file_path):
                    downloaded_files.append(file_path)
        elif post.is_video:
            temp_filename = f"video.{post.video_url.split('.')[-1].split('?')[0]}" # Attempt to get ext
            file_path = os.path.join(post_specific_dir, temp_filename if temp_filename else "video.mp4")
            L.download_url(post.video_url, filename=file_path)
            if os.path.exists(file_path):
                 downloaded_files.append(file_path)
        else: # Single image
            temp_filename = f"image.{post.url.split('.')[-1].split('?')[0]}"
            file_path = os.path.join(post_specific_dir, temp_filename if temp_filename else "image.jpg")
            L.download_url(post.url, filename=file_path)
            if os.path.exists(file_path):
                 downloaded_files.append(file_path)

        logger.info(f"Finished processing Instagram post from: {url}. Found {len(downloaded_files)} media files.")
        return downloaded_files if downloaded_files else None

    except Exception as e:
        logger.error(f"Error downloading from Instagram {url}: {e}")
        return None
    finally:
        # This cleanup logic needs to be invoked by the caller AFTER sending files.
        # For now, this function focuses on download. Caller should manage cleanup.
        pass


# ---------------------------------
# Check for FFmpeg
# ---------------------------------
def check_ffmpeg_installed():
    """Check if FFmpeg is installed."""
    if shutil.which("ffmpeg") is None:
        logger.warning("FFmpeg is not installed.")
        return False
    logger.info("FFmpeg is installed.")
    return True

# ----------------------------------
# Format Table for Available Formats (CLI Specific)
# ----------------------------------
# This function uses ANSI colors and tabulate, suitable for CLI, not Telegram.
# Kept for potential non-bot use, but not called by bot handlers.
def print_format_table_cli(info):
    # ... (original implementation with print and tabulate)
    pass

# ---------------------------------
# Load Configuration
# ---------------------------------
CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
    "default_format": "show_all", # Seems unused by bot
    "download_directory": "media",
    "history_file": "download_history.csv", # Bot doesn't use this directly for logging user downloads
    "mp3_quality": "192",
}

def load_config():
    """Load or create configuration file safely."""
    if not os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(DEFAULT_CONFIG, f, indent=4)
            logger.info(f"Created default config file: {CONFIG_FILE}")
        except IOError as e:
            logger.error(f"Error creating config file {CONFIG_FILE}: {e}")
            return DEFAULT_CONFIG # Return defaults if creation fails

    try:
        with open(CONFIG_FILE, "r") as f:
            config_data = json.load(f)
            # Validate essential keys
            for key, value in DEFAULT_CONFIG.items():
                if key not in config_data:
                    logger.warning(f"Key '{key}' not found in config. Using default: '{value}'")
                    config_data[key] = value
            return config_data
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Invalid or unreadable config file {CONFIG_FILE}: {e}. Using defaults.")
        try:
            with open(CONFIG_FILE, "w") as f: # Attempt to rewrite with defaults
                json.dump(DEFAULT_CONFIG, f, indent=4)
        except IOError as e_write:
            logger.error(f"Error resetting config file {CONFIG_FILE}: {e_write}")
        return DEFAULT_CONFIG

config = load_config()
main_download_directory = config.get("download_directory", DEFAULT_CONFIG["download_directory"])
# history_file = config.get("history_file", DEFAULT_CONFIG["history_file"]) # Bot doesn't use this
mp3_quality_config = config.get("mp3_quality", DEFAULT_CONFIG["mp3_quality"])

if not str(mp3_quality_config).isdigit() or int(mp3_quality_config) not in [64, 128, 192, 256, 320]: # Adjusted valid qualities
    logger.warning(f"Invalid MP3 quality in config: {mp3_quality_config}. Using default 192.")
    mp3_quality_config = "192"
else:
    mp3_quality_config = str(mp3_quality_config)


def get_unique_filename(filepath):
    """Ensure downloaded files are renamed if duplicates exist. Input is full path."""
    if not os.path.exists(filepath):
        return filepath
    
    base, ext = os.path.splitext(filepath)
    counter = 1
    new_filepath = f"{base} ({counter}){ext}"
    while os.path.exists(new_filepath):
        counter += 1
        new_filepath = f"{base} ({counter}){ext}"
    return new_filepath


# -----------------------------------------------------------
# Download Functions for Youtube and other platforms (CLI Specific)
# -----------------------------------------------------------
# This function is CLI-oriented and not used by the bot's URL_message.
def download_general_video_cli(url):
    # ... (original implementation with input() and print_format_table_cli)
    pass

# ---------------------------------
# Telegram Bot Handlers
# ---------------------------------

async def quality_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # ... (initial setup like before) ...
    user_id = query.from_user.id
    callback_data_str = query.data
    
    try:
        format_id, url_from_callback = callback_data_str.split("|", 1)
    except ValueError:
        await query.edit_message_text("Error: Malformed callback data.")
        return

    video_session_data = user_format_data.get(user_id)
    if not video_session_data or video_session_data.get("url") != url_from_callback:
        await query.edit_message_text("Session expired or data mismatch. Please send the link again.")
        return

    title = video_session_data.get("title", "video")
    selected_format_details = video_session_data["formats"].get(format_id)

    if not selected_format_details:
        await query.edit_message_text("Selected format details not found. Please try again.")
        return

    direct_media_url = selected_format_details.get('url')
    can_send_directly = (
        direct_media_url and
        selected_format_details.get('vcodec') != 'none' and
        selected_format_details.get('acodec') != 'none' and # Check if it's a combined video/audio format
        not (selected_format_details.get('protocol') in ['m3u8', 'm3u8_native', 'dash']) # Manifests often don't work
    )

    # Attempt to send via URL first
    if can_send_directly:
        await query.edit_message_text(f"⬇️ Attempting to send '{title}' directly by URL...")
        try:
            logger.info(f"Attempting to send by URL: {direct_media_url} for chat {query.message.chat.id}")
            await context.bot.send_video(
                chat_id=query.message.chat.id,
                video=direct_media_url,
                caption=f"{title} (direct from source)",
                connect_timeout=20, # Shorter timeout for URL check
                read_timeout=60    # Timeout for Telegram to download
            )
            await query.edit_message_text(f"✅ Sent '{title}' directly from source!")
            # Clean user session data for this URL
            if user_id in user_format_data and user_format_data[user_id].get("url") == url_from_callback:
                del user_format_data[user_id]
            return # Success!
        except Exception as e:
            logger.warning(f"Failed to send by URL ({direct_media_url}): {e}. Falling back to local download.")
            await query.edit_message_text(f"⚠️ Direct send failed. Attempting local download for '{title}'...")
            # Fall through to local download logic

    # --- Fallback to Local Download (existing logic) ---
    await query.edit_message_text(f"⬇️ Downloading '{title}' locally in selected quality...")
    # ... (rest of your existing local download logic using ydl_opts, ydl.download, send_document, and cleanup)
    # Ensure user_dl_dir, sane_title, output_template are defined here if not sent directly
    user_dl_dir = os.path.join(main_download_directory, str(user_id), "youtube")
    os.makedirs(user_dl_dir, exist_ok=True)
    sane_title = "".join(c if c.isalnum() or c in " ._-" else "_" for c in title)
    output_template = os.path.join(user_dl_dir, f"{sane_title}.%(ext)s")
    
    temp_download_path = None

    try:
        # Ensure selected_format_details.get('height', 1080) is valid if used in format string
        format_height = selected_format_details.get('height', 1080) if selected_format_details else 1080

        ydl_opts = {
            "format": f"{format_id}+bestaudio/bestvideo[height<=?{format_height}]+bestaudio/best",
            "outtmpl": output_template,
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
            "noprogress": True,
            "concurrent_fragment_downloads": 5, # Added for potential speed up
        }
        # ... (your existing local download, send_document, and cleanup logic follows) ...
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url_from_callback, download=True)
            temp_download_path = ydl.prepare_filename(info_dict) 

        if temp_download_path and os.path.exists(temp_download_path):
            final_download_path = get_unique_filename(temp_download_path) 
            if temp_download_path != final_download_path:
                 os.rename(temp_download_path, final_download_path)

            await query.edit_message_text("✅ Local download completed. Sending file...")
            try:
                with open(final_download_path, "rb") as f:
                    await context.bot.send_document(chat_id=query.message.chat.id, document=f, caption=title, connect_timeout=60, read_timeout=60)
                await query.edit_message_text(f"Sent: {title}") 
            except Exception as send_err:
                logger.error(f"Failed to send YT video {final_download_path}: {send_err}")
                await query.edit_message_text(f"❌ Error sending file: {send_err}")
            finally:
                if os.path.exists(final_download_path): 
                    os.remove(final_download_path)
                if user_id in user_format_data and user_format_data[user_id].get("url") == url_from_callback:
                    del user_format_data[user_id]
        else:
            await query.edit_message_text(f"❌ Local download failed or file not found: {title}")

    except Exception as e:
        logger.error(f"Error in local YT download for {url_from_callback}: {e}")
        await query.edit_message_text(f"❌ Error downloading video locally: {e}")



# For user tracking (in-memory, resets on restart)
unique_users = set()
user_counter = 0

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global user_counter # Modifying global
    user = update.effective_user
    if not user: return

    if user.id not in unique_users:
        unique_users.add(user.id)
        user_counter += 1
        logger.info(f"New user: {user.id} (@{user.username}), Total users: {user_counter}")

        if BOT_OWNER_ID: # Only send if owner ID is configured
            user_info_msg = (
                f"👤 New User Interacted\n"
                f"ID: {user.id}\n"
                f"Username: @{user.username if user.username else 'N/A'}\n"
                f"Name: {user.full_name}\n"
                f"Total Unique Users: {user_counter}"
            )
            try:
                await context.bot.send_message(chat_id=BOT_OWNER_ID, text=user_info_msg)
            except Exception as e:
                logger.error(f"Failed to send new user info to owner {BOT_OWNER_ID}: {e}")

    welcome_message = (
        f"👤 Hello, {user.first_name}!\n"
        f"I can help you download videos from various platforms.\n"
        f"Simply send me a link to get started."
    )
    if update.message:
        await update.message.reply_text(welcome_message)

def detect_platform(url: str) -> str:
    parsed_url = urlparse(url)
    domain = parsed_url.netloc.lower()
    path = parsed_url.path.lower()

    if domain.endswith("youtube.com") or domain.endswith("youtu.be"):
        return "YouTube"
    elif domain.endswith("instagram.com"):
        return "Instagram"
    elif domain.endswith("tiktok.com"):
        return "TikTok"
    elif domain.endswith("facebook.com") or domain.endswith("fb.watch"):
        return "Facebook"
    elif domain.endswith("twitter.com") or domain.endswith("x.com"):
        return "Twitter/X"
    elif domain.endswith("reddit.com") or domain.endswith("v.redd.it"): # v.redd.it is common for videos
        return "Reddit"
    elif domain.endswith("threads.net"):
        return "Threads"
    elif domain.endswith("pinterest.com") or domain.endswith("pin.it"):
        return "Pinterest"
    elif domain.endswith("linkedin.com") and "/feed/update/" in path : # More specific for posts
        return "LinkedIn"
    # Add more specific checks based on yt_dlp's supported extractors if needed
    elif any(d in domain for d in ["twitch.tv", "vimeo.com", "streamable.com", "bilibili.tv", "bilibili.com", "odysee.com", "rumble.com"]):
        return "Generic yt_dlp" # Use yt_dlp generic download
    else:
        # Fallback: Check if yt_dlp can handle it by trying to get extractor key
        try:
            extractor_key = yt_dlp.YoutubeDL({'quiet': True}).extract_info(url, download=False, process=False).get('extractor_key')
            if extractor_key:
                logger.info(f"URL {url} identified by yt_dlp as {extractor_key}")
                return f"Generic yt_dlp ({extractor_key})"
        except Exception:
            pass # Could not determine
        return "Unknown"


async def URL_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    if not update.effective_user or not update.effective_chat:
        return

    url = update.message.text.strip()
    platform = detect_platform(url)
    user_id_str = str(update.effective_user.id)

    await update.message.reply_text(f"🔗 Platform: <b>{platform}</b>\nProcessing link...", parse_mode="HTML")

    if platform == "Instagram":
        if not IG_USERNAME or not IG_PASSWORD:
            await update.message.reply_text("❌ Instagram downloads are currently unavailable (configuration missing).")
            return

        # Ensure base download directory exists
        user_ig_base_dir = os.path.join(main_download_directory, "instagram_media")
        os.makedirs(user_ig_base_dir, exist_ok=True)

        downloaded_media_files = download_instagram_post(url, user_ig_base_dir, user_id_str)
        
        post_shortcode = "" # Extract shortcode for cleanup path
        path_parts = urlparse(url).path.strip('/').split('/')
        if "p" in path_parts: shortcode_idx = path_parts.index("p") + 1
        elif "reel" in path_parts: shortcode_idx = path_parts.index("reel") + 1
        elif "reels" in path_parts: shortcode_idx = path_parts.index("reels") + 1
        else: shortcode_idx = -1
        if shortcode_idx != -1 and shortcode_idx < len(path_parts): post_shortcode = path_parts[shortcode_idx]

        post_specific_dir_to_clean = os.path.join(user_ig_base_dir, user_id_str, post_shortcode) if post_shortcode else None


        if downloaded_media_files:
            await update.message.reply_text(f"Found {len(downloaded_media_files)} media item(s). Sending now...")
            for file_path in downloaded_media_files:
                try:
                    with open(file_path, "rb") as f:
                        await context.bot.send_document(chat_id=update.effective_chat.id, document=f, connect_timeout=60, read_timeout=60)
                except Exception as e:
                    logger.error(f"Failed to send Instagram file {file_path}: {e}")
                    await update.message.reply_text(f"❌ Failed to send a file: {os.path.basename(file_path)}")
            await update.message.reply_text("✅ All Instagram media sent.")
        else:
            await update.message.reply_text("❌ Could not download Instagram content or no media found.")
        
        # Cleanup the specific post directory for this user
        if post_specific_dir_to_clean and os.path.isdir(post_specific_dir_to_clean):
            try:
                shutil.rmtree(post_specific_dir_to_clean)
                logger.info(f"Cleaned up Instagram directory: {post_specific_dir_to_clean}")
            except Exception as e:
                logger.error(f"Error cleaning up Instagram directory {post_specific_dir_to_clean}: {e}")
    
    elif platform == "YouTube" or platform.startswith("Generic yt_dlp"):
        if not check_ffmpeg_installed(): # For merging formats if needed
            await update.message.reply_text("⚠️ FFmpeg is not installed on the server. Some formats may not be available or video/audio might not merge.")
            # Decide if you want to proceed or stop. For now, it proceeds.
        if not check_internet_connection():
            await update.message.reply_text("❌ No internet connection on the server.")
            return
        
        try:
            await update.message.reply_text("Fetching video information from YouTube/platform...")
            ydl_opts_info = {'quiet': True, 'noplaylist': True} # Basic opts for info extraction
            with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
                info = ydl.extract_info(url, download=False)

            video_title = info.get("title", "video")
            formats = info.get("formats", [])
            buttons = []
            format_map_for_user = {} # To store format details for callback

            # Filter for formats with both video and audio, or good quality video-only / audio-only
            # Prefer mp4 if available.
            # This example prioritizes formats with video and audio combined (usually smaller list)
            # A more complex logic might list video-only and audio-only separately if user wants to pick
            
            # Sort formats by resolution (height) then by filesize (smaller preferred for same res)
            # formats.sort(key=lambda f: (f.get('height', 0) or 0, f.get('filesize', 0) or float('inf')), reverse=True)


            processed_formats = [] # (display_text, format_id, height)
            for fmt in formats:

                filesize_mb = (fmt.get('filesize') or fmt.get('filesize_approx') or 0) / (1024*1024)
                # Skip if filesize is effectively 0.00 MB
                if filesize_mb < 0.01: # Excludes 0.00MB and very tiny files
                    continue

                if fmt.get('vcodec') != 'none' and fmt.get('acodec') != 'none': # Video with audio
                    res = fmt.get('format_note', f"{fmt.get('height')}p" if fmt.get('height') else "video")
                    filesize_mb = (fmt.get('filesize') or fmt.get('filesize_approx') or 0) / (1024*1024)
                    display_text = f"{res} ({fmt.get('ext')}, {filesize_mb:.2f}MB)"
                    processed_formats.append((display_text, fmt['format_id'], fmt.get('height', 0)))
                elif fmt.get('vcodec') != 'none' and fmt.get('acodec') == 'none': # Video only
                    res = fmt.get('format_note', f"{fmt.get('height')}p" if fmt.get('height') else "video-only")
                    filesize_mb = (fmt.get('filesize') or fmt.get('filesize_approx') or 0) / (1024*1024)
                    display_text = f"{res} (V-Only, {fmt.get('ext')}, {filesize_mb:.2f}MB)"
                    processed_formats.append((display_text, fmt['format_id'], fmt.get('height', 0)))
                # Could add audio-only here too if desired

            # Sort by height (descending)
            processed_formats.sort(key=lambda x: x[2], reverse=True)
            
            # Create buttons, max ~10-15 to avoid overly large Telegram messages
            max_buttons = 10
            for display_text, format_id, _ in processed_formats[:max_buttons]:
                buttons.append([InlineKeyboardButton(text=display_text, callback_data=f"{format_id}|{url}")])
                format_map_for_user[format_id] = next((f for f in formats if f['format_id'] == format_id), {})


            if not buttons:
                await update.message.reply_text("❌ No suitable download formats found or video is protected.")
                return

            user_format_data[update.effective_user.id] = {
                "url": url,
                "formats": format_map_for_user, # Store details of selectable formats
                "title": video_title
            }

            reply_markup = InlineKeyboardMarkup(buttons)
            await update.message.reply_text(f"🎞 Select quality for '{video_title}':", reply_markup=reply_markup)
            
        except yt_dlp.utils.DownloadError as de:
            logger.error(f"yt_dlp DownloadError for {url}: {de}")
            if "copyright" in str(de).lower() or "private video" in str(de).lower():
                 await update.message.reply_text("❌ This video is private, copyrighted, or otherwise unavailable for download.")
            else:
                 await update.message.reply_text(f"❌ Failed to fetch video info: {de}")
        except Exception as e:
            logger.error(f"Error processing YouTube/Generic URL {url}: {e}")
            await update.message.reply_text(f"❌ An unexpected error occurred: {e}")

    else:
        await update.message.reply_text("❌ This platform is not directly supported yet, or the URL is not recognized.")


async def post_init(application: ApplicationBuilder):
    """Create download directory if it doesn't exist."""
    os.makedirs(main_download_directory, exist_ok=True)
    logger.info(f"Download directory '{main_download_directory}' ensured.")
    # You could also perform one-time FFmpeg check here, but bot might run in envs where user can't install.
    # check_ffmpeg_installed()

if __name__ == "__main__":
    if not BOT_TOKEN:
        logger.critical("BOT_TOKEN is not set. Cannot start the bot.")
    else:
        app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

        app.add_handler(CommandHandler("start", start_command))
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), URL_message))
        app.add_handler(CallbackQueryHandler(quality_selection_callback))

        logger.info("Bot is starting...")
        app.run_polling()
        logger.info("Bot has stopped.")