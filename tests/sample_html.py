"""Sample HTML mimicking a VK archive export page."""

SAMPLE_HTML = """<!DOCTYPE html>
<html><head><style>
  body { background: url('https://pp.userapi.com/c846418/v846418123/1a1b2c/style_bg.JPG?size=1280x720&amp;quality=95'); }
</style></head><body>
  <img src="https://pp.userapi.com/c846418/v846418123/1a1b2d/photo_01.jpg">
  <img src="https://sun9-1.userapi.com/impf/abc/DSC_0002.JPEG?size=2560x1707&amp;quality=95&amp;sign=deadbeef&amp;type=album">
  <a href="https://vk.com/away.php?to=https%3A%2F%2Fexample.com%2Fsong.mp3">audio</a>
  <a href="https://cs9-11.userapi.net/d1/sound/song.m4a?extra=xyz">m4a</a>
  <video src="https://cdn.vk.me/vid/clip.mp4"></video>
  <p>user typed junk: https://www.avi and https://notrealjpg</p>
  <p>relative and anchor: /local/file.png#fragment</p>
  <a href="https://vk.ru/video123456_7890">video page</a>
  <a href="https://youtu.be/dQw4w9WgXcQ">yt</a>
  <img src="https://cdn.example.com/pic(1).png">
</body></html>
"""
