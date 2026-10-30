import os, re, json, base64, time, secrets, threading, ipaddress, socket, sqlite3, smtplib, hashlib, hmac, shutil, datetime, zlib, struct, html as htmllib
from urllib.parse import urlparse, urljoin, parse_qs
import random
import copy
import requests
from werkzeug.exceptions import HTTPException
from email.message import EmailMessage
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, request, Response, send_file, send_from_directory, abort, jsonify

try:
    from requests.compat import chardet as _chardet
except Exception:  # pragma: no cover
    _chardet = None

app = Flask(__name__)
HERE = os.path.dirname(os.path.abspath(__file__))
UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36")
UA_DESKTOP = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# ---------------------------------------------------------------
#  Kod hissələrə bölünüb (parts/ qovluğu). Hamısı bu faylın qlobal
#  məkanında işləyir, ona görə davranış əvvəlki tək fayl ilə eynidir.
#  Sıra vacibdir: yuxarıdan aşağıya.
# ---------------------------------------------------------------
PARTS = ['parts/01_rooms.py', 'parts/02_accounts.py', 'parts/03_game-invites.py', 'parts/04_friends.py', 'parts/05_room-roles-misc.py', 'parts/06_youtube-search.py', 'parts/07_proxy.py', 'parts/08_admin.py', 'parts/09_ip-ban.py', 'parts/10_domino.py', 'parts/11_durak-engine.py', 'parts/12_durak-room.py', 'parts/13_game-voice.py', 'parts/14_chess.py', 'parts/15_chess-bot.py']
for _p in PARTS:
    _fp = os.path.join(HERE, _p)
    with open(_fp, encoding="utf-8") as _f:
        exec(compile(_f.read(), _fp, "exec"), globals())


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), threaded=True)
