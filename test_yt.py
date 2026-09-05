import urllib.parse as urlparse
from youtube_transcript_api import YouTubeTranscriptApi

url = "https://www.youtube.com/watch?v=G9hS4u-Cl5w&t=2s"
parsed = urlparse.urlparse(url)
video_id = urlparse.parse_qs(parsed.query).get('v', [None])[0]

ytt_api = YouTubeTranscriptApi()
t_list = ytt_api.list(video_id)
t_obj = next(iter(t_list))

transcript = t_obj.fetch()
text_lines = [t.text for t in transcript]
full_text = " ".join(text_lines)
print("Transcript snippet:", full_text[:200])
