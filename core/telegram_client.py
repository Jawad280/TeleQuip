import asyncio
from telegram import Bot, InputFile

class TelegramClient:
    def __init__(self, token):
        self.bot = Bot(token=token)

    async def send_message_to_person(self, chat_id, text, **kwargs):
        return await self.bot.send_message(chat_id=chat_id, text=text, **kwargs)

    async def send_message_to_group(self, chat_id, text, **kwargs):
        return await self.bot.send_message(chat_id=chat_id, text=text, **kwargs)

    async def send_poll(self, chat_id, question, options):
        return await self.bot.send_poll(chat_id, question, options)
    
    async def send_gif_to_person(self, chat_id, gif_path, caption=None, ttl=None, **kwargs):
        """Send a GIF to a user in private chat with optional self-delete after ttl seconds."""
        with open(gif_path, "rb") as f:
            gif_file = InputFile(f, filename="animation.gif")
            message = await self.bot.send_animation(chat_id=chat_id, animation=gif_file, caption=caption, **kwargs)

        # Schedule deletion if ttl is provided
        if ttl is not None:
            asyncio.create_task(self._delete_message_after(chat_id, message.message_id, ttl))

        return message

    async def _delete_message_after(self, chat_id, message_id, ttl):
        await asyncio.sleep(ttl)
        try:
            await self.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            # message might already be deleted, or TTL expired, ignore
            pass
