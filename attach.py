import io
import re

import discord

FALLBACK_NAME = "image.png"

def safe_filename(name):
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", name or FALLBACK_NAME)
    return cleaned[-60:] or FALLBACK_NAME

class Picture:

    def __init__(self, data, filename):
        self.data = data
        self.filename = safe_filename(filename)

    @property
    def reference(self):
        return f"attachment://{self.filename}"

    def file(self):
        return discord.File(io.BytesIO(self.data), filename=self.filename)

async def read_image(attachment):
    if attachment is None:
        return None, None

    if not (attachment.content_type or "").startswith("image/"):
        return None, "that attachment is not an image."

    try:
        data = await attachment.read()
    except discord.HTTPException:
        return None, "i could not read that image."

    return Picture(data, attachment.filename), None