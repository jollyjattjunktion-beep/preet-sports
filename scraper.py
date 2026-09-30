import json
import logging
import re
from typing import Any, Dict

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("crex_scraper")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://crex.com/",
}


def clean(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def first_match(pattern: str, text: str, flags=re.I) -> str:
    m = re.search(pattern, text or "", flags)
    return clean(m.group(1)) if m else ""


def normalize_img(src: str) -> str:
    src = clean(src)
    return "https:" + src if src.startswith("//") else src


def classify_ball(txt: str) -> str:
    t = txt.lower().strip()
    if t == "4":
        return "four"
    if t == "6":
        return "six"
    if t == "w":
        return "wicket"
    if t in {"wd", "nb", "lb", "b"}:
        return "extra"
    if re.fullmatch(r"\d+", t):
        return "run"
    return "dot"


def parse_score(text: str) -> str:
    m = re.search(r"\b(\d{1,4})\s*[-/]\s*(\d{1,2})\b", clean(text))
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def parse_overs(text: str) -> str:
    text = clean(text)
    for p in [
        r"\((\d{1,3}\.\d)\)",
        r"\b(\d{1,3}\.\d{1,2})\s*overs?\b",
        r"\b(\d{1,3}\.\d)\b",
    ]:
        m = re.search(p, text, re.I)
        if m:
            return f"({m.group(1)})" if p.startswith(r"\(") else m.group(1)
    return ""


def parse_runs_balls(text: str):
    m = re.search(r"\b(\d{1,3})\s*\((\d{1,3})\)", clean(text))
    return (m.group(1), m.group(2)) if m else ("", "")


def make_player(name="", runs="0", balls="0", fours="0", sixes="0",
                sr="0.00", img="", on_strike=False):
    return {
        "name": clean(name),
        "short_name": clean(name),
        "runs": clean(runs or "0"),
        "balls": clean(balls or "0"),
        "fours": clean(fours or "0"),
        "sixes": clean(sixes or "0"),
        "sr": clean(sr or "0.00"),
        "img": normalize_img(img),
        "on_strike": bool(on_strike),
    }


def extract_player_data(page) -> Dict[str, Any]:
    """
    CREX changes CSS class names periodically, so this uses multiple
    selector families and validates the visible text before accepting data.
    """
    batsmen = []
    bowler = None
    seen = set()

    selectors = [
        '[class*="batsmen-partnership"]',
        '[class*="batsman-partnership"]',
        '[class*="batter-card"]',
        '[class*="batsman-card"]',
        '[class*="player-card"]',
        '[class*="batsmen-score"]',
    ]

    blocks = []
    for selector in selectors:
        try:
            blocks.extend(page.locator(selector).all())
        except Exception:
            pass

    for block in blocks:
        try:
            text = clean(block.inner_text(timeout=1200))
        except Exception:
            continue

        if not text or len(text) > 1200:
            continue

        name = ""
        for sel in [
            '[class*="p-name"]',
            '[class*="player-name"]',
            '[class*="batsmen-name"]',
            '[class*="batsman-name"]',
            '[class*="batter-name"]',
        ]:
            try:
                loc = block.locator(sel).first
                if loc.count():
                    name = clean(loc.inner_text(timeout=500))
                    if name:
                        break
            except Exception:
                pass

        if not name:
            for line in [clean(x) for x in text.splitlines() if clean(x)][:8]:
                if (
                    re.search(r"[A-Za-z]", line)
                    and not re.search(r"^(4s|6s|SR|Econ|Overs?|Runs?|Wickets?)\b", line, re.I)
                    and not re.fullmatch(r"[\d().*/\-\s]+", line)
                ):
                    name = line
                    break

        if not name:
            continue

        img = ""
        try:
            img_loc = block.locator("img").first
            if img_loc.count():
                img = normalize_img(
                    img_loc.get_attribute("src")
                    or img_loc.get_attribute("data-src")
                    or ""
                )
        except Exception:
            pass

        runs, balls = parse_runs_balls(text)
        fours = first_match(r"4s\s*:\s*(\d+)", text) or first_match(r"\b4s\s+(\d+)\b", text)
        sixes = first_match(r"6s\s*:\s*(\d+)", text) or first_match(r"\b6s\s+(\d+)\b", text)
        sr = first_match(r"\bSR\s*:\s*([\d.]+)", text) or first_match(r"\bSR\s+([\d.]+)\b", text)

        figures = first_match(r"\b(\d{1,2}-\d{1,3})\s*\((\d{1,3}\.\d)\)", text)
        econ = first_match(r"Econ\s*:\s*([\d.]+)", text) or first_match(r"\bEcon\s+([\d.]+)\b", text)

        if econ or figures:
            if not bowler:
                overs = first_match(r"\((\d{1,3}\.\d)\)", text)
                bowler = {
                    "name": name,
                    "short_name": name,
                    "figures": figures or "",
                    "overs": f"({overs})" if overs else "",
                    "runs": (figures.split("-")[1] if "-" in figures else ""),
                    "wickets": (figures.split("-")[0] if "-" in figures else ""),
                    "econ": econ or "",
                    "img": img,
                    "summary": f"Econ: {econ} | ({overs})" if econ or overs else "",
                }
            continue

        if not runs:
            continue

        try:
            cls = clean(block.get_attribute("class"))
        except Exception:
            cls = ""

        low = text.lower()
        on_strike = (
            "*" in text
            or "striker" in low
            or "on-strike" in cls.lower()
            or "active" in cls.lower()
        )

        key = (name.lower(), runs, balls)
        if key in seen:
            continue
        seen.add(key)

        batsmen.append(
            make_player(
                name=name,
                runs=runs,
                balls=balls,
                fours=fours or "0",
                sixes=sixes or "0",
                sr=sr or "0.00",
                img=img,
                on_strike=on_strike,
            )
        )

    return {
        "batsman1": batsmen[0] if len(batsmen) > 0 else None,
        "batsman2": batsmen[1] if len(batsmen) > 1 else None,
        "bowler": bowler,
    }


def _team_full_name(name: str) -> str:
    """Normalize common CREX team abbreviations to display names."""
    key = clean(name).lower()
    mapping = {
        "sa": "South Africa", "rsa": "South Africa", "south africa": "South Africa",
        "aus": "Australia", "australia": "Australia",
        "eng": "England", "england": "England",
        "sl": "Sri Lanka", "sri lanka": "Sri Lanka",
        "ind": "India", "india": "India",
        "wi": "West Indies", "west indies": "West Indies",
        "zim": "Zimbabwe", "zimbabwe": "Zimbabwe",
        "pak": "Pakistan", "pakistan": "Pakistan",
        "nz": "New Zealand", "new zealand": "New Zealand",
        "ban": "Bangladesh", "bangladesh": "Bangladesh",
        "afg": "Afghanistan", "afghanistan": "Afghanistan",
        "ire": "Ireland", "ireland": "Ireland",
        "nep": "Nepal", "nepal": "Nepal",
    }
    return mapping.get(key, clean(name))


def _valid_team_name(name: str) -> bool:
    n = clean(name)
    if not n or len(n) > 50:
        return False
    bad = (
        "search match", "details", "crr", "rrr", "target", "live score",
        "match info", "scorecard", "discussions", "series stats",
        "opt to", "need ", "from ", "overs"
    )
    return not any(x in n.lower() for x in bad)


def extract_teams(page, body_text: str, title: str) -> Dict[str, Any]:
    """Extract teams from the CREX match header first.

    CREX's DOM contains navigation/search text that can be picked up by
    generic team-name selectors. Prefer the actual score header and only
    use DOM selectors as a last resort.
    """
    text = clean(body_text)
    page_title = clean(title)

    team1 = {"name": "", "short": "", "full_name": "", "score": "", "overs": "", "logo": "", "target": ""}
    team2 = {"name": "", "short": "", "full_name": "", "score": "", "overs": "", "logo": "", "target": ""}

    # Example actual CREX header:
    # SA 261-5 (39.4) (Marco Jansen 0(1), David Miller 98(71)) vs Australia 2nd-ODI
    header_sources = [page_title, text]
    for source in header_sources:
        # Batting side with live score followed by "vs" and opponent name.
        m = re.search(
            r"\b([A-Z]{2,5})\s+(\d{1,4})\s*[-/]\s*(\d{1,2})\s*"
            r"\((\d{1,3}\.\d)\).*?\bvs\b\s+"
            r"([A-Za-z][A-Za-z .&'-]{1,40}?)(?=\s+(?:\d+(?:st|nd|rd|th)[ -]?(?:ODI|T20I|T20|Test)|[-|]|$))",
            source,
            re.I,
        )
        if m:
            short1 = clean(m.group(1)).upper()
            short2_raw = clean(m.group(5))
            # Remove common trailing competition text if regex captured it.
            short2_raw = re.split(r"\s+(?:2nd|1st|3rd|4th|5th)\s+", short2_raw, maxsplit=1, flags=re.I)[0]
            if _valid_team_name(short2_raw):
                team1.update({
                    "name": short1,
                    "short": short1,
                    "full_name": _team_full_name(short1),
                    "score": f"{m.group(2)}/{m.group(3)}",
                    "overs": f"({m.group(4)})",
                })
                team2.update({
                    "name": short2_raw,
                    "short": short2_raw.upper() if len(short2_raw) <= 5 else short2_raw,
                    "full_name": _team_full_name(short2_raw),
                })
                break

    # More general match-header fallback: "South Africa ... vs Australia".
    if not team1["full_name"] or not team2["full_name"]:
        for source in header_sources:
            m = re.search(
                r"\b(South Africa|Australia|England|Sri Lanka|India|West Indies|"
                r"Zimbabwe|Pakistan|New Zealand|Bangladesh|Afghanistan|Ireland|Nepal)\b"
                r".*?\bvs\b\s*"
                r"(South Africa|Australia|England|Sri Lanka|India|West Indies|"
                r"Zimbabwe|Pakistan|New Zealand|Bangladesh|Afghanistan|Ireland|Nepal)\b",
                source,
                re.I,
            )
            if m:
                if not team1["full_name"]:
                    team1["full_name"] = _team_full_name(m.group(1))
                    team1["name"] = team1["full_name"]
                    team1["short"] = next((k.upper() for k,v in {
                        "sa":"South Africa","aus":"Australia","eng":"England","sl":"Sri Lanka",
                        "ind":"India","wi":"West Indies","zim":"Zimbabwe","pak":"Pakistan",
                        "nz":"New Zealand","ban":"Bangladesh","afg":"Afghanistan","ire":"Ireland","nep":"Nepal"
                    }.items() if v.lower() == team1["full_name"].lower()), team1["full_name"])
                if not team2["full_name"]:
                    team2["full_name"] = _team_full_name(m.group(2))
                    team2["name"] = team2["full_name"]
                    team2["short"] = team2["full_name"]
                break

    # Last-resort DOM extraction, but reject CREX navigation/search pollution.
    if not team1["full_name"] or not team2["full_name"]:
        names = []
        try:
            for el in page.locator('[class*="team-name"], [class*="team-title"], [class*="team-name-wrapper"]').all():
                txt = clean(el.inner_text(timeout=400))
                if _valid_team_name(txt) and txt not in names:
                    names.append(txt)
        except Exception:
            pass
        for i, name in enumerate(names[:2]):
            target = team1 if i == 0 else team2
            if not target["full_name"]:
                target["full_name"] = _team_full_name(name)
                target["name"] = name
                target["short"] = name

    # Extract scores/overs from the actual team-name parent only if needed.
    try:
        team_els = page.locator('[class*="team-name"]').all()
        for el in team_els:
            txt_name = clean(el.inner_text(timeout=300))
            if not _valid_team_name(txt_name):
                continue
            parent = el.locator("xpath=..")
            txt = clean(parent.inner_text(timeout=400))
            score = parse_score(txt)
            overs = parse_overs(txt)
            target = team1 if (
                txt_name.upper() == team1["short"].upper()
                or _team_full_name(txt_name).lower() == team1["full_name"].lower()
            ) else team2
            if score and not target["score"]:
                target["score"] = score
            if overs and not target["overs"]:
                target["overs"] = overs
            try:
                img = parent.locator("img").first
                if img.count():
                    src = img.get_attribute("src") or img.get_attribute("data-src") or ""
                    if src and not target["logo"]:
                        target["logo"] = normalize_img(src)
            except Exception:
                pass
    except Exception:
        pass

    # If the page says "AUS opt to Bowl", team1 is batting and team2 is bowling.
    # Do not invent a second score when CREX only supplied the batting score.
    return {"team1": team1, "team2": team2}



def extract_dismissal_type(body_text: str, last_wicket: str = "", data_text: str = "") -> str:
    """
    Extract the dismissal type shown by CREX for the latest wicket.

    This is intentionally isolated from score/over/player extraction.
    It returns only a dismissal label when CREX actually exposes one.
    """
    dismissal_patterns = [
        ("Run Out", r"\brun\s*out\b"),
        ("Caught", r"\bcaught\b"),
        ("Bowled", r"\bbowled\b"),
        ("LBW", r"\blbw\b"),
        ("Stumped", r"\bstumped\b"),
        ("Hit Wicket", r"\bhit\s+wicket\b"),
        ("Obstructing the Field", r"\bobstructing\s+the\s+field\b"),
        ("Retired Hurt", r"\bretired\s+hurt\b"),
        ("Retired Out", r"\bretired\s+out\b"),
    ]

    sources = []

    # The visible CREX "Last Wkt" area is the most useful place to look.
    text = clean(body_text)
    if text:
        lw_pos = -1
        if last_wicket:
            lw_pos = text.lower().find(clean(last_wicket).lower())

        if lw_pos >= 0:
            # Include the surrounding CREX wicket/result area.
            sources.append(text[max(0, lw_pos - 700): lw_pos + 900])

        # Also inspect the tail because the current live-event panel can
        # appear near the end of the rendered body text.
        sources.append(text[-2500:])

    # Application JSON/scripts can contain the exact same dismissal label.
    if data_text:
        sources.append(clean(data_text)[-5000:])

    # Prefer Run Out / other labels found near the latest-wicket area.
    for source in sources:
        for label, pattern in dismissal_patterns:
            if re.search(pattern, source, re.I):
                return label

    return ""


def extract_stats(body_text: str, team1: Dict[str, Any], team2: Dict[str, Any]):
    crr = first_match(r"\bCRR\s*[:\-]\s*([0-9]+(?:\.[0-9]+)?)", body_text)
    rrr = first_match(r"\bRRR\s*[:\-]\s*([0-9]+(?:\.[0-9]+)?)", body_text)

    target = first_match(r"\bTarget\s*[:\-]?\s*(\d+)", body_text)
    if not target:
        target = first_match(r"\bneed\s+(\d+)\s*(?:runs?)?", body_text)

    partnership = first_match(
        r"(?:P'?ship|Partnership)\s*[:\-]?\s*(\d+\s*\(\s*\d+\s*\))",
        body_text,
    )

    last_wicket = first_match(
        r"Last\s+Wkt\s*[:\-]?\s*(.+?)(?=\s+(?:Over|P'?ship|CRR|RRR|Target)\b|$)",
        body_text,
    )

    equation = ""
    for pattern in [
        # Start with a letter so stray numeric text such as "58 AUS" is never included.
        r"\b([A-Za-z][A-Za-z &.-]*?\s+opt\s+to\s+(?:Bat|Bowl))\b",
        r"\b((?:Need|Target)\s+\d+(?:\s+runs?)?(?:\s+from\s+\d+(?:\.\d+)?\s+overs?)?)\b",
        r"\b([A-Za-z][A-Za-z &.-]*?\s+won\s+by\s+[^|]+?)\b",
    ]:
        equation = first_match(pattern, body_text)
        if equation:
            equation = re.sub(r"^\d+\s+", "", equation).strip()
            break

    # Calculate CRR only if CREX did not expose it.
    if not crr and team1.get("score") and team1.get("overs"):
        m = re.search(r"(\d+)\s*/\s*\d+", team1["score"])
        ov = re.search(r"(\d+)\.(\d+)", team1["overs"])
        if m and ov:
            balls = int(ov.group(1)) * 6 + int(ov.group(2))
            if balls:
                crr = f"{int(m.group(1)) * 6 / balls:.2f}"

    return {
        "crr": crr or None,
        "rrr": rrr or None,
        "target": target or None,
        "partnership": partnership or None,
        "last_wicket": last_wicket or None,
        "equation": equation,
    }


def extract_overs(page, body_text: str):
    structured = []

    try:
        blocks = page.locator(
            '[class*="overs-slide"], [class*="over-slide"], [class*="over-card"]'
        ).all()
    except Exception:
        blocks = []

    for block in blocks:
        try:
            txt = clean(block.inner_text(timeout=500))
        except Exception:
            continue

        if not txt:
            continue

        label = first_match(r"\b(Over\s+\d+)\b", txt) or "Over"
        balls = []

        try:
            for node in block.locator(
                '[class*="over-ball"], [class*="ball"]'
            ).all():
                bt = clean(node.inner_text(timeout=250))
                if bt and len(bt) <= 5 and re.fullmatch(
                    r"(?:\d+|wd|nb|lb|w|W|b|Wd|Nb)", bt, re.I
                ):
                    balls.append({"text": bt, "type": classify_ball(bt)})
        except Exception:
            pass

        total = first_match(r"=\s*([0-9]+)\s*$", txt)
        if balls:
            structured.append({
                "label": label,
                "balls": balls,
                "total": f"= {total}" if total else "",
            })

    # Text fallback.
    if not structured:
        matches = re.findall(
            r"(Over\s+\d+)\s+"
            r"((?:\d+|wd|nb|lb|w|W)(?:\s+(?:\d+|wd|nb|lb|w|W)){1,8})"
            r"\s*(?:=\s*(\d+))?",
            body_text,
            re.I,
        )
        for label, ball_text, total in matches[-6:]:
            structured.append({
                "label": label,
                "balls": [
                    {"text": b, "type": classify_ball(b)}
                    for b in ball_text.split()
                ],
                "total": f"= {total}" if total else "",
            })

    current = structured[-1] if structured else None
    last_ball = current["balls"][-1]["text"] if current and current.get("balls") else ""

    recent = []
    for ov in structured[-2:]:
        recent.append(
            f"{ov['label']} "
            + " ".join(x["text"] for x in ov["balls"])
            + (f" {ov['total']}" if ov.get("total") else "")
        )

    return {
        "current_over": current,
        "last_ball": last_ball,
        "structured_overs": structured,
        "recent_overs": " | ".join(recent),
    }


def scrape_crex_match(url: str):
    if not url or not str(url).strip():
        return {"success": False, "error": "No Crex URL provided."}

    url = str(url).strip()
    logger.info("Scraping CREX with Playwright: %s", url)

    browser = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--no-zygote",
                    "--single-process",
                ],
            )

            context = browser.new_context(
                user_agent=HEADERS["User-Agent"],
                locale="en-US",
                viewport={"width": 1440, "height": 1000},
                extra_http_headers=HEADERS,
            )

            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)

            try:
                page.wait_for_load_state("networkidle", timeout=12000)
            except PlaywrightTimeoutError:
                pass

            # Allow Angular/React live-score components to hydrate.
            page.wait_for_timeout(5000)

            try:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1200)
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(1200)
            except Exception:
                pass

            body_text = clean(page.locator("body").inner_text(timeout=5000))
            title = clean(page.title())

            # Some CREX data can exist inside application JSON.
            json_blobs = []
            try:
                for s in page.locator("script").all():
                    try:
                        txt = s.text_content(timeout=200)
                        if txt and any(
                            k in txt
                            for k in ("batsman", "batsmen", "bowler", "partnership", "CRR")
                        ):
                            json_blobs.append(txt)
                    except Exception:
                        pass
            except Exception:
                pass

            data_text = body_text + "\n" + "\n".join(json_blobs)

            teams = extract_teams(page, body_text, title)
            stats = extract_stats(data_text, teams["team1"], teams["team2"])
            dismissal_type = extract_dismissal_type(
                body_text,
                stats["last_wicket"] or "",
                data_text,
            )
            players = extract_player_data(page)
            overs = extract_overs(page, data_text)

            if stats["target"]:
                teams["team2"]["target"] = stats["target"]

            vs_text = ""
            mvs = re.search(
                r"([A-Za-z][A-Za-z .&-]+)\s+vs\s+([A-Za-z][A-Za-z .&-]+)",
                title,
                re.I,
            )
            if mvs:
                vs_text = f"{clean(mvs.group(1))} vs {clean(mvs.group(2))}"

            match_title = first_match(
                r"\b(\d+(?:st|nd|rd|th)\s+(?:ODI|T20I|T20|Test|One Day))\b",
                title,
            )

            venue = first_match(
                r"(?:The\s+)?([A-Z][A-Za-z' -]+(?:Stadium|Ground|Oval|Arena))",
                body_text,
            )

            status = stats["equation"] or first_match(
                r"\b(Batter Injured|Rain Delay|Stumps|Innings Break|"
                r"Match Abandoned|Match Delayed)\b",
                body_text,
            )

            current_run = overs["last_ball"]

            return {
                "success": True,
                "fetch_method": "playwright_robust",
                "url": url,
                "vs_text": vs_text,
                "match_title": match_title,
                "series_name": "",
                "venue": venue,
                "full_header": title,
                "team1": teams["team1"],
                "team2": teams["team2"],
                "status": status,
                "equation": stats["equation"],
                "crr": stats["crr"],
                "rrr": stats["rrr"],
                "target": stats["target"],
                "partnership": stats["partnership"],
                "last_wicket": stats["last_wicket"],
                "dismissal_type": dismissal_type or None,
                "current_run": current_run,
                "last_ball": current_run,
                "current_over": overs["current_over"],
                "structured_overs": overs["structured_overs"],
                "recent_overs": overs["recent_overs"],
                "batsman1": players["batsman1"],
                "batsman2": players["batsman2"],
                "bowler": players["bowler"],
                "win_pct": None,
                "lose_pct": None,
                "debug": {
                    "body_chars": len(body_text),
                    "player1_found": bool(players["batsman1"]),
                    "player2_found": bool(players["batsman2"]),
                    "bowler_found": bool(players["bowler"]),
                    "crr_found": bool(stats["crr"]),
                    "rrr_found": bool(stats["rrr"]),
                    "partnership_found": bool(stats["partnership"]),
                    "last_wicket_found": bool(stats["last_wicket"]),
                    "dismissal_type_found": bool(dismissal_type),
                    "overs_found": bool(overs["structured_overs"]),
                },
            }

    except PlaywrightTimeoutError as e:
        logger.exception("CREX Playwright timeout")
        return {"success": False, "error": f"CREX page timeout: {e}", "url": url}
    except Exception as e:
        logger.exception("CREX scraper error")
        return {"success": False, "error": f"CREX scraper error: {e}", "url": url}
    finally:
        try:
            if browser:
                browser.close()
        except Exception:
            pass


if __name__ == "__main__":
    import sys
    test_url = sys.argv[1] if len(sys.argv) > 1 else ""
    print(json.dumps(scrape_crex_match(test_url), indent=2, ensure_ascii=False))
