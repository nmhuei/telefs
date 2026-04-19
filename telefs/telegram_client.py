"""Telegram client wrapper using Telethon."""
import asyncio
import os
import warnings
from pathlib import Path
from typing import Optional, Tuple, Any

from telethon import TelegramClient, errors
from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto

from .config import get_api_credentials, get_config_dir, get_phone_number

SESSION_PATH = get_config_dir() / "telefs_session"


class TelegramFSClient:
    def __init__(self):
        self.client: Optional[TelegramClient] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Suppress Telethon's warning about already authorized session
        warnings.filterwarnings("ignore", category=UserWarning, module="telethon.client.auth")

    def _get_loop(self):
        if self._loop is None:
            try:
                self._loop = asyncio.get_event_loop()
            except RuntimeError:
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
        return self._loop

    async def _init_client(self):
        api_id, api_hash = get_api_credentials()
        if not api_id or not api_hash:
            raise ValueError("API credentials missing. Please run setup first.")
            
        self.client = TelegramClient(str(SESSION_PATH), api_id, api_hash)
        phone = get_phone_number()
        # Add timeout to start
        try:
            await asyncio.wait_for(self.client.start(phone=phone), timeout=30)
        except asyncio.TimeoutError:
            raise ConnectionError("Telegram connection timed out.")
        return self.client

    def _run_async(self, coro):
        loop = self._get_loop()
        if loop.is_running():
            # If already in an event loop (e.g. nested calls), we might have issues
            # but for a CLI it's usually fine to run until complete if not already running.
            # However, if we're in a real async app, this would need different handling.
            # For TeleFS CLI, this is the safest sync-to-async bridge.
            import nest_asyncio
            nest_asyncio.apply()
        return loop.run_until_complete(coro)

    def connect(self):
        """Connect and authenticate."""
        return self._run_async(self._init_client())

    async def _upload_file(self, local_path: Path, progress_callback=None) -> Tuple[int, str, str]:
        """Upload a file to Saved Messages. Return (message_id, peer_id, file_id)."""
        message = await self.client.send_file(
            'me',
            str(local_path),
            progress_callback=progress_callback,
            force_document=True
        )
        
        file_id = ""
        if isinstance(message.media, MessageMediaDocument):
            file_id = str(message.media.document.id)
        elif isinstance(message.media, MessageMediaPhoto):
            file_id = str(message.media.photo.id)
            
        return message.id, 'me', file_id

    def upload_file(self, local_path: Path, progress_callback=None) -> Tuple[int, str, str]:
        """Synchronous wrapper for upload."""
        return self._run_async(self._upload_file(local_path, progress_callback))

    async def _upload_bytes(self, data: bytes, filename: str, progress_callback=None) -> int:
        """Upload raw bytes as a file. Return message_id."""
        import io
        file = io.BytesIO(data)
        file.name = filename
        message = await self.client.send_file(
            'me',
            file,
            progress_callback=progress_callback,
            force_document=True
        )
        return message.id

    def upload_bytes(self, data: bytes, filename: str, progress_callback=None) -> int:
        """Synchronous wrapper for upload_bytes."""
        return self._run_async(self._upload_bytes(data, filename, progress_callback))

    async def _download_file(self, message_id: int, peer_id: str, output_path: Path, progress_callback=None):
        """Download a file using its message_id."""
        message = await self.client.get_messages(peer_id, ids=message_id)
        if not message or not message.media:
            raise ValueError(f"Message {message_id} not found or has no media.")
            
        await self.client.download_media(
            message,
            file=str(output_path),
            progress_callback=progress_callback
        )

    def download_file(self, message_id: int, peer_id: str, output_path: Path, progress_callback=None):
        """Synchronous wrapper for download."""
        return self._run_async(self._download_file(message_id, peer_id, output_path, progress_callback))

    async def _download_bytes(self, message_id: int, progress_callback=None) -> bytes:
        """Download message media as bytes."""
        message = await self.client.get_messages('me', ids=message_id)
        if not message or not message.media:
            raise ValueError(f"Message {message_id} not found or no media.")
        
        import io
        buffer = io.BytesIO()
        await self.client.download_media(message, file=buffer, progress_callback=progress_callback)
        return buffer.getvalue()

    def download_bytes(self, message_id: int, progress_callback=None) -> bytes:
        """Synchronous wrapper for download_bytes."""
        return self._run_async(self._download_bytes(message_id, progress_callback))

    async def _delete_messages(self, peer_id: str, message_ids: list):
        """Delete messages from Telegram."""
        await self.client.delete_messages(peer_id, message_ids)

    def delete_messages(self, peer_id: str, message_ids: list):
        """Synchronous wrapper for delete."""
        return self._run_async(self._delete_messages(peer_id, message_ids))

    async def _disconnect(self):
        """Asynchronous disconnect core."""
        if self.client:
            await self.client.disconnect()

    def disconnect(self):
        """Safe disconnection wrapper."""
        if self.client:
            try:
                self._run_async(self._disconnect())
            except Exception:
                pass # Already disconnected or closing
            self.client = None
