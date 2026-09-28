"""Parsing das respostas de Pexels/Pixabay (sem rede)."""

from broll_bot.http import HttpClient
from broll_bot.models import MediaType
from broll_bot.providers.pexels import PexelsProvider, _slug_description
from broll_bot.providers.pixabay import PixabayProvider
from broll_bot.ratelimit import RetryPolicy, TokenBucket


class FakeHttp(HttpClient):
    def __init__(self, payload):
        super().__init__("fake", TokenBucket(100, 10), RetryPolicy(0))
        self.payload = payload
        self.params = None

    def get_json(self, url, params=None):
        self.params = params
        return self.payload


PEXELS_VIDEOS = {"videos": [{
    "id": 857195, "width": 3840, "height": 2160, "duration": 14,
    "url": "https://www.pexels.com/video/man-typing-on-laptop-857195/",
    "image": "https://images.pexels.com/videos/857195/thumb.jpeg",
    "user": {"name": "Fulano"},
    "video_files": [
        {"quality": "sd", "file_type": "video/mp4", "width": 960, "height": 540, "link": "https://v/sd.mp4"},
        {"quality": "hd", "file_type": "video/mp4", "width": 1920, "height": 1080, "link": "https://v/hd.mp4"},
        {"quality": "uhd", "file_type": "video/mp4", "width": 3840, "height": 2160, "link": "https://v/4k.mp4"},
    ],
}]}


def test_pexels_picks_smallest_file_that_reaches_target():
    http = FakeHttp(PEXELS_VIDEOS)
    p = PexelsProvider("key", http, target_width=1920)
    [c] = p.search("man typing laptop", MediaType.VIDEO, 15)
    assert c.download_url == "https://v/hd.mp4"
    assert c.width == 1920 and c.duration == 14
    assert "laptop" in c.tags
    assert http.params["orientation"] == "landscape"


def test_pexels_locale_follows_query_language():
    http = FakeHttp({"videos": []})
    PexelsProvider("key", http).search("cidade à noite", MediaType.VIDEO, 15, language="pt")
    assert http.params["locale"] == "pt-BR"


def test_slug_description():
    assert _slug_description("https://www.pexels.com/video/aerial-view-of-city-123/") == "aerial view of city"


PIXABAY_VIDEOS = {"hits": [{
    "id": 125, "pageURL": "https://pixabay.com/videos/id-125/", "tags": "city, night, traffic",
    "duration": 12, "user": "beltran",
    "videos": {
        "large": {"url": "https://p/large.mp4", "width": 1920, "height": 1080, "thumbnail": "https://p/l.jpg"},
        "medium": {"url": "https://p/medium.mp4", "width": 1280, "height": 720, "thumbnail": "https://p/m.jpg"},
        "small": {"url": "https://p/small.mp4", "width": 960, "height": 540},
    },
}]}


def test_pixabay_video_parsing():
    http = FakeHttp(PIXABAY_VIDEOS)
    p = PixabayProvider("key", http, target_width=1920)
    [c] = p.search("city night", MediaType.VIDEO, 2)
    assert c.download_url == "https://p/large.mp4"
    assert c.tags == ["city", "night", "traffic"]
    assert c.thumbnail_url == "https://p/m.jpg"
    assert http.params["per_page"] == 3  # mínimo aceito pela API


def test_pixabay_image_parsing():
    http = FakeHttp({"hits": [{"id": 9, "tags": "coffee, cup", "largeImageURL": "https://p/big.jpg",
                               "webformatURL": "https://p/web.jpg", "imageWidth": 4000, "imageHeight": 2600}]})
    [c] = PixabayProvider("key", http).search("coffee", MediaType.IMAGE, 5)
    assert c.media_type is MediaType.IMAGE and c.download_url == "https://p/big.jpg"
    assert http.params["image_type"] == "photo"


def test_providers_unavailable_without_key():
    assert not PexelsProvider(None, FakeHttp({})).available()
    assert not PixabayProvider("", FakeHttp({})).available()
