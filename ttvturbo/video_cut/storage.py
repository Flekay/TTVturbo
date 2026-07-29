from pathlib import Path
from ttvturbo.media_capabilities.storage import CapabilityStorage


class VideoCutStorage(CapabilityStorage):
    def __init__(self, root: Path) -> None:
        super().__init__(root, label="video-cut")
