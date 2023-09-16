import requests
from urllib.parse import quote
import cv2


class RadioJavan:

    _search_url = "https://api-rjvn.app/api2/search?query={query}"
    _audio_url = "https://api-rjvn.app/api2/mp3?id={id}"
    _video_url = "https://api-rjvn.app/api2/video/{id}"


    def get_video_time(self, url):
        video = cv2.VideoCapture(url)
        frames = video.get(cv2.CAP_PROP_FRAME_COUNT)
        fps = video.get(cv2.CAP_PROP_FPS)
        return int(frames / fps)

    def search(self, query):
        res = requests.get(self._search_url.format(query=quote(query)))
        data = res.json()
        for i in data['mp3s']:
            yield {
                "id": i["id"],
                "artist": i["artist"],
                "title": i["song"],
                "link": i["link"],
                "thumbnail": i["photo"],
                "type": "audio"
                
            }
        for i in data['videos']:
            yield {
                "id": i["id"],
                "artist": i["artist"],
                "title": i["song"],
                "link": i["link"],
                "thumbnail": i["photo"],
                "type": "video"
                
            }


    def get_audio(self, audio_id):
        res = requests.get(self._audio_url.format(id=audio_id)).json()
        return {
            "id": res["id"],
            "artist": res["artist"],
            "title": res["song"],
            "duration": int(res["duration"]),
            "link": res["link"],
            "thumbnail": res["photo"],
            "type": "audio"
        }


    def get_video(self, video_id):
        res = requests.get(self._video_url.format(id=video_id)).json()
        return {
            "id": res["id"],
            "artist": res["artist"],
            "title": res["song"],
            "duration": self.get_video_time(res["link"]),
            "link": res["link"],
            "thumbnail": res["photo"],
            "type": "video"
        }
