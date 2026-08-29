# -*- coding: utf-8 -*-
import requests

# 模拟浏览器 fetch：字符串体 + 默认 text/plain 头（geoApi 未设 Content-Type 时的形态）
body = '{"count":10,"brand_id":1}'
r = requests.post(
    "http://127.0.0.1:5080/api/knowledge/docs/3/keywords",
    data=body.encode("utf-8"),
    headers={"Content-Type": "text/plain;charset=UTF-8"},
    timeout=180)
print("text/plain 复现:", r.status_code, r.json().get("message"))
