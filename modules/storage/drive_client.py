"""Google Drive 整合（尚未實作，預留擴充點）。

設計備註（未來實作時參考）：

  OAuth2 Scope:
    https://www.googleapis.com/auth/drive.file
    （只能存取本 app 建立的檔案，免 Google verification）

  憑證檔：
    config/gdrive_client_secret.json
    config/gdrive_token.json

  Drive 目錄結構：
    AI-Channel-Episodes/
      <slug>/
        script/     script.json, research.json, prompts
        audio/      audio_full.wav, section_*.wav
        images/     section_*.png, thumbnail.png
        video/      final.mp4, subtitles.ass

  環境變數：
    GDRIVE_ROOT_FOLDER_ID   選填，指定根資料夾 ID
    DRIVE_ENABLED=0         關閉整合

  待實作函式：
    _get_credentials()                                # 仿 youtube_uploader
    _build_drive()
    get_or_create_root_folder() -> str
    get_or_create_episode_folder(slug) -> str
    upload_file(local_path, slug, subfolder) -> dict
    upload_episode_bundle(slug) -> dict
    list_episodes() -> list[dict]
    download_file(drive_id, local_path) -> Path
    delete_episode_folder(slug) -> bool

  相依套件：google-api-python-client, google-auth-oauthlib
  （YouTube 上傳已引入，通常無須另裝）
"""
from __future__ import annotations


def is_enabled() -> bool:
    """目前永遠回傳 False（未實作）。"""
    return False
