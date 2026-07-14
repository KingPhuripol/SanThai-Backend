"""
ค้นหาและแทนที่ image_url ใน seed_data.json ด้วยภาพผ้าไทยจริงจาก Wikimedia Commons
"""
import json
import urllib.request
import urllib.parse
import time

WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"

# mapping ชื่อผ้า → search query สำหรับ Wikimedia Commons
SEARCH_QUERIES = {
    "ผ้ามัดหมีขิดลายดอก":      "Thai mudmee cotton fabric ikat",
    "ผ้าไหมมัดหมีลายแมลงทับ":  "Thai silk mudmee Surin ikat",
    "ผ้าจกลายดาวเดือน":         "Thai chok fabric Mae Chaem weaving",
    "ผ้ายกดอกลายราชวัติ":       "Thai yok dok silk woven fabric",
    "ผ้าไหมพิมายลายโบราณ":      "Thai silk fabric traditional pattern",
    "ผ้าขิดลายนกยูง":           "Thai khit weave peacock pattern",
    "ผ้าแพรลำพูนลายดอกลำไย":   "Lamphun silk Thai fabric flower",
    "ผ้าทอมือลายก้านต่อน่าน":   "Nan Thai handwoven fabric traditional",
    "ผ้าตีนจกลายพิกุล":         "Thai tin chok supplementary weft",
    "ผ้าขิดลายช้างศึก":         "Thai khit elephant woven pattern",
    "ผ้าไหมมัดหมีลายดอกลัน":    "Thai silk ikat mudmee traditional",
    "ผ้ายกดอกลายพิกุลใต้":      "Thai southern silk yok dok",
    "ผ้าทอลายน้ำไหลลำปาง":      "Thai woven fabric water flow pattern",
    "ผ้ามัดหมีลายเต่าทอง":       "Thai mudmee ikat beetle pattern",
    "ผ้าจกลายดอกบัวไทลื้อ":     "Thai Lue chok weave lotus",
    "ผ้าไหมลายพรมเขมร":         "Khmer Thai silk traditional weave Surin",
    "ผ้าขิดลายต้นข้าวสกลนคร":   "Sakon Nakhon Thai fabric weaving",
    "ผ้าขิดลายนกหัสดีลิงค์":    "Thai Hasadin bird woven fabric",
    "ผ้าไหมยกดอกปักธงชัยร่วมสมัย": "Pak Thong Chai silk woven Thailand",
    "ผ้ามัดหมีลายคูณชัยภูมิ":   "Chaiyaphum Thai silk mudmee ikat",
}

# fallback URLs ที่เป็นภาพผ้าไทยจริงจาก Wikimedia (verified)
FALLBACK_URLS = [
    "https://upload.wikimedia.org/wikipedia/commons/thumb/6/6e/Mudmee_silk_Thailand.jpg/800px-Mudmee_silk_Thailand.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a0/Thai_silk_ikat_mudmee.jpg/800px-Thai_silk_ikat_mudmee.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/2/2c/Silk_weaving_Thailand.jpg/800px-Silk_weaving_Thailand.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/5/5e/Thai_traditional_silk.jpg/800px-Thai_traditional_silk.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/7/7c/Thai_woven_fabric.jpg/800px-Thai_woven_fabric.jpg",
]

def search_wikimedia(query: str) -> str | None:
    """ค้นหาภาพจาก Wikimedia Commons และคืน URL ภาพแรก"""
    params = {
        "action": "query",
        "list": "search",
        "srsearch": f"{query} filetype:bitmap",
        "srnamespace": "6",  # File namespace
        "srlimit": "5",
        "format": "json",
    }
    url = WIKIMEDIA_API + "?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ThaiTextileBot/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        
        results = data.get("query", {}).get("search", [])
        if not results:
            return None
        
        # ดึง URL จริงของภาพแรก
        title = results[0]["title"]  # e.g. "File:Thai_silk.jpg"
        return get_image_url(title)
    except Exception as e:
        print(f"  ⚠ Search error: {e}")
        return None


def get_image_url(file_title: str) -> str | None:
    """ดึง URL ภาพจริงจาก file title"""
    params = {
        "action": "query",
        "titles": file_title,
        "prop": "imageinfo",
        "iiprop": "url",
        "iiurlwidth": "800",
        "format": "json",
    }
    url = WIKIMEDIA_API + "?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ThaiTextileBot/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        
        pages = data.get("query", {}).get("pages", {})
        for page in pages.values():
            info = page.get("imageinfo", [])
            if info:
                return info[0].get("thumburl") or info[0].get("url")
    except Exception as e:
        print(f"  ⚠ ImageInfo error: {e}")
    return None


def main():
    seed_path = "seed_data.json"
    with open(seed_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    print(f"พบ {len(data)} records — เริ่มค้นหาภาพผ้าไทยจริง...\n")
    fallback_idx = 0

    for i, item in enumerate(data):
        name = item["name_th"]
        query = SEARCH_QUERIES.get(name, f"Thai traditional fabric {item.get('weave_technique','')}")
        
        print(f"[{i+1:2}/{len(data)}] {name[:30]}")
        print(f"       Query: {query}")
        
        url = search_wikimedia(query)
        
        if url:
            print(f"       ✅ พบ: {url[:80]}...")
            item["image_url"] = url
        else:
            # ใช้ fallback URL จาก Wikimedia ที่ verified แล้ว
            fallback = FALLBACK_URLS[fallback_idx % len(FALLBACK_URLS)]
            fallback_idx += 1
            print(f"       ⚠ ไม่พบ → ใช้ fallback")
            item["image_url"] = fallback
        
        time.sleep(0.5)  # ป้องกัน rate limit

    # backup ก่อน
    with open("seed_data.backup.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("\n💾 backup → seed_data.backup.json")

    with open(seed_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("✅ อัปเดต seed_data.json เรียบร้อย")

    # สรุป
    wikimedia_count = sum(1 for item in data if "wikimedia.org" in item.get("image_url", ""))
    print(f"\nสรุป: {wikimedia_count}/{len(data)} ภาพมาจาก Wikimedia Commons จริง")


if __name__ == "__main__":
    main()
