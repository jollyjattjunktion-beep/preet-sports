import re
import requests
from bs4 import BeautifulSoup
import json
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("crex_scraper")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://crex.com/",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

def _get_match_score(team_short: str, comp_name: str) -> int:
    """Calculates similarity score between team abbreviation and competitor full name."""
    ts = re.sub(r'[^a-zA-Z]', '', team_short or '').lower()
    cn = re.sub(r'[^a-zA-Z]', '', comp_name or '').lower()
    if not ts or not cn:
        return 0
    if ts in cn or cn in ts:
        return 10
    initials = ''.join(w[0] for w in (comp_name or '').split() if w).lower()
    if ts == initials or initials in ts or ts in initials:
        return 8
    if len(ts) >= 3 and ts[:3] in cn:
        return 6
    if len(ts) >= 2 and ts[:2] in cn:
        return 4
    return 0

def scrape_crex_match(url: str):
    """
    Scrapes real live match data from a given Crex match URL.
    Contains NO hardcoded or mock match data. Only extracts what is present on the page.
    """
    if not url or not str(url).strip():
        return {
            "success": False,
            "error": "No Crex URL provided. Please provide a real match link to scrape."
        }

    url = str(url).strip()
    logger.info(f"Scraping live Crex URL: {url}")

    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        logger.error(f"Error fetching URL {url}: {e}")
        return {
            "success": False,
            "error": f"Failed to fetch match URL: {str(e)}",
            "url": url
        }

    soup = BeautifulSoup(html, "html.parser")

    # ── 1. JSON-LD SportsEvent extraction ──
    sports_event = None
    for s in soup.find_all("script"):
        if s.get("type") == "application/ld+json" and s.string:
            try:
                d = json.loads(s.string)
                if isinstance(d, dict) and d.get("@type") == "SportsEvent":
                    sports_event = d
                    break
            except Exception:
                pass

    competitors = sports_event.get("competitor", []) if sports_event else []
    event_name = sports_event.get("name", "") if sports_event else ""
    event_status = sports_event.get("eventStatus", "") if sports_event else ""
    venue = sports_event.get("location", {}).get("name", "") if sports_event else ""
    series_img = sports_event.get("image", "") if sports_event else ""

    # ── 2. Match header info, series, & title ──
    h1 = soup.find("h1")
    h1_text = h1.get_text(" ", strip=True) if h1 else ""
    header_el = soup.find("div", class_="live-score-header")
    header_text = header_el.get_text(" ", strip=True) if header_el else ""

    series_name = ""
    match_title = ""
    vs_text = ""

    title_source = h1_text or header_text
    if title_source:
        cleaned_title = re.sub(r"\s+(live|summary|match updates).*$", "", title_source, flags=re.IGNORECASE).strip()
        parts = [p.strip() for p in cleaned_title.split(",") if p.strip()]
        if len(parts) >= 3:
            vs_text = parts[0]
            match_title = parts[1]
            series_name = parts[2]
        elif len(parts) == 2:
            vs_text = parts[0]
            match_title = parts[1]
        elif len(parts) == 1:
            vs_text = parts[0]

    # ── 3. Teams, Scores, Logos, Status from Score Card ──
    card = soup.find("div", class_="live-score-card")
    team1 = {
        "name": "",
        "short": "",
        "full_name": "",
        "score": "",
        "overs": "",
        "logo": ""
    }
    team2 = {
        "name": "",
        "short": "",
        "full_name": "",
        "score": "",
        "overs": "",
        "logo": "",
        "target": ""
    }
    status = ""
    center_digit = ""

    if card:
        # Team 1 (first inning block in card)
        t1_div = card.find("div", class_="team-inning")
        if t1_div:
            img = t1_div.find("img")
            if img and img.get("src"):
                team1["logo"] = img["src"]
            name_el = t1_div.find(class_=re.compile("team-name"))
            if name_el:
                team1["name"] = name_el.get_text(strip=True)
                team1["short"] = team1["name"]
            score_el = t1_div.find(class_=re.compile("runs"))
            if score_el:
                over_span = score_el.find(class_="over-text")
                overs_txt = over_span.get_text(strip=True) if over_span else ""
                runs_txt = score_el.get_text(" ", strip=True).replace(overs_txt, "").strip()
                team1["score"] = runs_txt
                team1["overs"] = overs_txt

        # Status / Center Digit
        res_box = card.find(class_=re.compile("result-box|team-result"))
        if res_box:
            txt = res_box.get_text(" ", strip=True)
            # If it's a single ball score (e.g. 0, 1, 4, 6, W, WD, NB), it's the center live ball indicator
            if len(txt) <= 3 and any(c.isdigit() or c in "WwNnBb" for c in txt):
                center_digit = txt
            elif txt:
                status = txt

        # Team 2 (second-inning block in card)
        t2_div = card.find("div", class_=re.compile("second-inning"))
        if t2_div:
            img = t2_div.find("img")
            if img and img.get("src"):
                team2["logo"] = img["src"]
            name_el = t2_div.find(class_=re.compile("team-name"))
            if name_el:
                raw2 = name_el.get_text(strip=True)
                # Avoid capturing the CRR/RRR wrapper if it appears inside team-name
                if not any(k in raw2 for k in ["CRR", "RRR", "need"]):
                    team2["name"] = raw2
                    team2["short"] = raw2
            score_el = t2_div.find(class_=re.compile("runs"))
            if score_el:
                txt = score_el.get_text(" ", strip=True)
                m = re.search(r"(\(\d+\.?\d*\))\s*([\d-]+)|([\d-]+)\s*(\(\d+\.?\d*\))", txt)
                if m:
                    if m.group(1):
                        team2["overs"] = m.group(1)
                        team2["score"] = m.group(2)
                    else:
                        team2["score"] = m.group(3)
                        team2["overs"] = m.group(4)
                elif txt:
                    team2["score"] = txt

    # Match vs_text components if team short names need resolving
    if vs_text and "vs" in vs_text.lower():
        vs_parts = [p.strip() for p in re.split(r"\s+vs\s+", vs_text, flags=re.IGNORECASE) if p.strip()]
        if len(vs_parts) == 2:
            p1, p2 = vs_parts[0], vs_parts[1]
            if team1["name"]:
                if _get_match_score(team1["name"], p1) >= _get_match_score(team1["name"], p2):
                    team1["short"] = p1
                    if not team2["short"]: team2["short"] = p2
                else:
                    team1["short"] = p2
                    if not team2["short"]: team2["short"] = p1
            elif not team1["short"] and not team2["short"]:
                team1["short"] = p1
                team2["short"] = p2

    # Map competitors from JSON-LD to team1 and team2
    if competitors and len(competitors) >= 2:
        c1, c2 = competitors[0], competitors[1]
        score_c1_t1 = _get_match_score(team1["short"] or team1["name"], c1.get("name", ""))
        score_c2_t1 = _get_match_score(team1["short"] or team1["name"], c2.get("name", ""))

        if score_c1_t1 >= score_c2_t1:
            comp_team1, comp_team2 = c1, c2
        else:
            comp_team1, comp_team2 = c2, c1

        team1["full_name"] = comp_team1.get("name", "")
        if not team1["logo"] and comp_team1.get("image"):
            team1["logo"] = comp_team1.get("image", "")

        team2["full_name"] = comp_team2.get("name", "")
        if not team2["name"]:
            team2["name"] = team2["short"] or comp_team2.get("name", "")
        if not team2["short"]:
            team2["short"] = team2["name"]
        if not team2["logo"] and comp_team2.get("image"):
            team2["logo"] = comp_team2.get("image", "")

    # If team 2's score was only in the JSON-LD event title (e.g. live chase match)
    if not team2["score"] and event_name:
        m2 = re.search(r"vs\s+([A-Za-z0-9\s-]+?)\s+(\d+-\d+)\s*(\(\(?[\d\.]+\)?\))", event_name)
        if m2:
            if not team2["name"]:
                team2["name"] = team2["short"] or m2.group(1).strip()
            team2["score"] = m2.group(2).strip()
            team2["overs"] = m2.group(3).strip()

    # ── 4. Target, CRR, RRR, Equation ──
    crr = None
    rrr = None
    trr = soup.find(class_=re.compile("team-run-rate"))
    if trr:
        m_crr = re.search(r"CRR\s*:\s*([\d\.]+)", trr.get_text())
        if m_crr: crr = m_crr.group(1)
        m_rrr = re.search(r"RRR\s*:\s*([\d\.]+)", trr.get_text())
        if m_rrr: rrr = m_rrr.group(1)

    equation = ""
    for fr in soup.find_all(class_=re.compile("final-result")):
        t = fr.get_text(" ", strip=True)
        if any(w in t.lower() for w in ["need", "won", "opt to", "trail", "lead", "stopped", "rain", "delay"]):
            equation = t
            if not status:
                status = t
            break

    # If status is still empty, check sports event name or status
    if not status:
        if "won by" in event_name.lower():
            status = event_name.split(",")[0].strip()
        elif event_status:
            status = event_status

    if not equation:
        equation = status

    # Compute CRR if not in DOM but real runs and overs exist
    if not crr and team1["score"] and team1["overs"]:
        try:
            r_num = float(re.findall(r"\d+", team1["score"])[0])
            ov_clean = team1["overs"].replace("(", "").replace(")", "").strip()
            ov_num = float(ov_clean)
            if ov_num > 0:
                crr = f"{(r_num / ov_num):.2f}"
        except Exception:
            pass

    # Extract target from team 2 score if applicable
    target_val = None
    if team2["score"]:
        try:
            t2_runs = int(re.findall(r"\d+", team2["score"])[0])
            target_val = str(t2_runs + 1)
            team2["target"] = target_val
        except Exception:
            pass

    # ── 5. Real Batsmen & Bowlers (NO fallback players) ──
    batsmen = []
    bowler = None

    partnerships = soup.find_all(class_="batsmen-partnership")
    for bp in partnerships:
        img_el = bp.find("img")
        img_url = img_el["src"] if img_el and img_el.get("src") else ""

        name_p = bp.find(class_="batsmen-name")
        full_name_p = bp.find(class_="p-name")
        p_name = full_name_p.get_text(strip=True) if full_name_p else (name_p.get_text(strip=True) if name_p else "")
        short_name = name_p.get_text(strip=True) if name_p else p_name

        score_div = bp.find(class_="batsmen-score")
        is_bowler = score_div and "bowler" in score_div.get("class", [])
        score_txt = score_div.get_text(" ", strip=True) if score_div else ""

        career_div = bp.find(class_="batsmen-career-wrapper")
        career_txt = career_div.get_text(" ", strip=True) if career_div else ""

        if is_bowler:
            econ_m = re.search(r"Econ:\s*([\d\.]+)", career_txt)
            econ = econ_m.group(1) if econ_m else ""
            fig_m = re.search(r"([\d]+-[\d]+)\s*(\([\d\.]+\))", score_txt)
            wickets_runs = fig_m.group(1) if fig_m else score_txt
            ov = fig_m.group(2) if fig_m else ""
            bowler = {
                "name": p_name,
                "short_name": short_name,
                "figures": wickets_runs,
                "overs": ov,
                "econ": econ,
                "img": img_url,
                "summary": f"Econ: {econ} | {ov}" if econ or ov else ""
            }
        else:
            fours_m = re.search(r"4s:\s*(\d+)", career_txt)
            sixes_m = re.search(r"6s:\s*(\d+)", career_txt)
            sr_m = re.search(r"SR:\s*([\d\.]+)", career_txt)
            runs_m = re.search(r"^(\d+)\s*\(([\d]+)\)", score_txt)
            runs = runs_m.group(1) if runs_m else "0"
            balls = runs_m.group(2) if runs_m else "0"
            is_striker = ("*" in score_txt or "*" in p_name or "striker" in bp.get("class", []) or len(batsmen) == 0)
            batsmen.append({
                "name": p_name,
                "short_name": short_name,
                "runs": runs,
                "balls": balls,
                "fours": fours_m.group(1) if fours_m else "0",
                "sixes": sixes_m.group(1) if sixes_m else "0",
                "sr": sr_m.group(1) if sr_m else "0.00",
                "img": img_url,
                "on_strike": is_striker
            })

    batsman1 = batsmen[0] if len(batsmen) > 0 else None
    batsman2 = batsmen[1] if len(batsmen) > 1 else None

    # ── 6. Real Overs & Ball-by-ball (NO fallback overs) ──
    recent_overs = []
    structured_overs = []
    overs_slides = soup.find_all(class_="overs-slide")
    for os in overs_slides:
        content = os.find(class_="content") or os
        label_el = content.find("span")
        ov_label = label_el.get_text(strip=True) if label_el else "Over"
        balls = []
        for b in content.find_all(class_=re.compile("over-ball")):
            txt = b.get_text(strip=True)
            if not txt:
                continue
            cls = "dot"
            if txt == "4":
                cls = "four"
            elif txt == "6":
                cls = "six"
            elif txt in ["wd", "nb", "w", "b", "lb"]:
                cls = "extra"
            elif "w" in txt.lower():
                cls = "wicket"
            elif txt in ["1", "2", "3"]:
                cls = "run"
            balls.append({"text": txt, "type": cls})
        tot_el = content.find(class_="total")
        total_txt = tot_el.get_text(strip=True) if tot_el else ""
        if balls:
            structured_overs.append({
                "label": ov_label,
                "balls": balls,
                "total": total_txt or f"= {sum(int(x['text']) for x in balls if x['text'].isdigit())}"
            })
        recent_overs.append(os.get_text(" ", strip=True))

    recent_str = " | ".join(recent_overs[-2:]) if recent_overs else ""
    current_over_data = structured_overs[-1] if structured_overs else None
    last_ball_txt = center_digit or (current_over_data["balls"][-1]["text"] if current_over_data and current_over_data.get("balls") else "")

    # ── 7. Last Wicket ──
    last_wkt_text = ""
    lw = soup.find(class_=re.compile(r"l-wicket|last-wicket|wicket-fall|fall-of-wicket"))
    if lw:
        last_wkt_text = lw.get_text(" ", strip=True).replace("Last Wkt :", "").replace("Last Wkt:", "").strip()

    # ── 8. Player of the Match (only if on page) ──
    potm_el = soup.find(class_=re.compile("player-of-match"))
    potm_text = potm_el.get_text(" ", strip=True) if potm_el else None

    # ── 9. Partnership ──
    pship_str = None
    if batsman1 and batsman2:
        try:
            pship_runs = int(batsman1["runs"]) + int(batsman2["runs"])
            pship_balls = int(batsman1["balls"]) + int(batsman2["balls"])
            pship_str = f"{pship_runs} ({pship_balls})"
        except Exception:
            pass

    # ── 10. Win / Lose Probability (Calculated from real match state only) ──
    win_pct = None
    lose_pct = None
    status_lower = (status or "").lower()
    t1_names = [team1.get("short", "").lower(), team1.get("name", "").lower(), team1.get("full_name", "").lower()]
    t1_names = [n for n in t1_names if n]
    t2_names = [team2.get("short", "").lower(), team2.get("name", "").lower(), team2.get("full_name", "").lower()]
    t2_names = [n for n in t2_names if n]

    if "won" in status_lower:
        if any(n in status_lower for n in t1_names):
            win_pct = "100%"
            lose_pct = "0%"
        elif any(n in status_lower for n in t2_names):
            win_pct = "0%"
            lose_pct = "100%"
    elif crr and rrr:
        try:
            crr_float = float(crr)
            rrr_float = float(rrr)
            prob = 1.0 / (1.0 + (rrr_float / max(1.0, crr_float)) ** 2)
            prob_pct = round(max(1.0, min(99.0, prob * 100)), 1)
            win_pct = f"{prob_pct}%"
            lose_pct = f"{round(100 - prob_pct, 1)}%"
        except Exception:
            pass

    return {
        "success": True,
        "url": url,
        "vs_text": vs_text,
        "match_title": match_title,
        "series_name": series_name,
        "venue": venue,
        "series_img": series_img,
        "full_header": header_text,
        "team1": team1,
        "team2": team2,
        "status": status,
        "equation": equation,
        "crr": crr,
        "rrr": rrr,
        "partnership": pship_str,
        "last_wicket": last_wkt_text or None,
        "win_pct": win_pct,
        "lose_pct": lose_pct,
        "current_over": current_over_data,
        "last_ball": last_ball_txt,
        "structured_overs": structured_overs,
        "batsman1": batsman1,
        "batsman2": batsman2,
        "bowler": bowler,
        "recent_overs": recent_str,
        "potm": potm_text,
    }

if __name__ == "__main__":
    import sys
    test_url = sys.argv[1] if len(sys.argv) > 1 else None
    if test_url:
        result = scrape_crex_match(test_url)
        print(json.dumps(result, indent=2))
    else:
        print("Please provide a Crex match URL to test.")
