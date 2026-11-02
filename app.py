# -*- coding: utf-8 -*-
"""
Birlikdə İzlə - server (Flask).

Kod parts/ qovluğundakı fayllara bölünüb və nömrə sırası ilə (01, 02, 03 ...)
EYNİ ad sahəsində (namespace) işə salınır. Beləliklə bütün qlobal dəyişənlər və
funksiyalar əvvəlki kimi bir-birini görür - heç bir məntiq dəyişməyib.

İşə salmaq:   gunicorn --workers 1 --threads 64 app:app
və ya:        python app.py

Yeni hissə əlavə etmək üçün parts/ qovluğuna  21_ad.py  kimi fayl qoyun.
"""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARTS = os.path.join(_HERE, "parts")

for _name in sorted(f for f in os.listdir(_PARTS) if f.endswith(".py")):
    _path = os.path.join(_PARTS, _name)
    with open(_path, encoding="utf-8") as _f:
        exec(compile(_f.read(), _path, "exec"), globals())
