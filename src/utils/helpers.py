"""Helper utilities for OpenSubtitles scraper"""

import re
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


def sanitize_filename(filename: str) -> str:
    """Sanitize filename for safe file operations"""
    # Remove or replace invalid characters
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    # Remove leading/trailing whitespace and dots
    filename = filename.strip(' .')
    # Limit length
    if len(filename) > 255:
        filename = filename[:255]
    return filename


def extract_imdb_id(text: str) -> Optional[str]:
    """Extract IMDB ID from text"""
    pattern = r'tt(\d{7,8})'
    match = re.search(pattern, text)
    if match:
        return f"tt{match.group(1)}"
    return None


def normalize_title(title: str) -> str:
    """Normalize movie/TV show title for comparison"""
    # Convert to lowercase
    title = title.lower()
    # Remove special characters and extra spaces
    title = re.sub(r'[^\w\s]', ' ', title)
    title = re.sub(r'\s+', ' ', title).strip()
    return title


def extract_year(text: str) -> Optional[int]:
    """Extract a plausible release year from text (e.g. title, filename)."""
    try:
        matches = re.findall(r'\b((?:19|20)\d{2})\b', text)
        if matches:
            years = [int(m) for m in matches]
            current_year = 2026
            valid_years = [y for y in years if 1900 <= y <= current_year + 1]
            if valid_years:
                return valid_years[-1]
        return None
    except Exception:
        return None


def build_url(base_url: str, path: str, params: Optional[Dict[str, Any]] = None) -> str:
    """Build URL with proper joining and parameters"""
    # Ensure base_url ends with / and path doesn't start with /
    base = base_url.rstrip('/')
    clean_path = path.lstrip('/')
    url = f"{base}/{clean_path}"
    
    if params:
        # URL encode parameters properly
        from urllib.parse import urlencode
        param_str = urlencode(params)
        if param_str:
            separator = '&' if '?' in url else '?'
            url = f"{url}{separator}{param_str}"
    
    return url


def extract_subtitle_info(filename: str) -> Dict[str, Any]:
    """Extract information from subtitle filename"""
    info = {
        'language': None,
        'hearing_impaired': False,
        'forced': False,
        'release_group': None,
        'quality': None
    }
    
    filename_lower = filename.lower()
    
    # Extract language codes - comprehensive list supporting all major languages
    # ISO 639-1 and 639-2 codes plus common language names
    lang_patterns = [
        # English
        r'\b(eng|en|english)\b',
        # Spanish
        r'\b(spa|es|spanish|espanol|castellano)\b',
        # French
        r'\b(fre|fr|french|francais)\b',
        # German
        r'\b(ger|de|german|deutsch)\b',
        # Italian
        r'\b(ita|it|italian|italiano)\b',
        # Portuguese
        r'\b(por|pt|portuguese|portugues)\b',
        # Russian
        r'\b(rus|ru|russian|russkiy)\b',
        # Chinese
        r'\b(chi|zh|chinese|mandarin|cantonese|zho|zht|zhs)\b',
        # Japanese
        r'\b(jpn|ja|japanese|nihongo)\b',
        # Korean
        r'\b(kor|ko|korean|hangul)\b',
        # Arabic
        r'\b(ara|ar|arabic|arabi)\b',
        # Dutch
        r'\b(dut|nl|dutch|nederlands)\b',
        # Polish
        r'\b(pol|pl|polish|polski)\b',
        # Swedish
        r'\b(swe|sv|swedish|svenska)\b',
        # Norwegian
        r'\b(nor|no|norwegian|norsk)\b',
        # Danish
        r'\b(dan|da|danish|dansk)\b',
        # Finnish
        r'\b(fin|fi|finnish|suomi)\b',
        # Greek
        r'\b(gre|el|greek|ellinika)\b',
        # Hebrew
        r'\b(heb|he|hebrew|ivrit)\b',
        # Turkish
        r'\b(tur|tr|turkish|turkce)\b',
        # Czech
        r'\b(cze|cs|czech|cesky)\b',
        # Hungarian
        r'\b(hun|hu|hungarian|magyar)\b',
        # Romanian
        r'\b(rum|ro|romanian|romana)\b',
        # Bulgarian
        r'\b(bul|bg|bulgarian|bulgarski)\b',
        # Croatian
        r'\b(hrv|hr|croatian|hrvatski)\b',
        # Serbian
        r'\b(srp|sr|serbian|srpski)\b',
        # Slovak
        r'\b(slo|sk|slovak|slovensky)\b',
        # Slovenian
        r'\b(slv|sl|slovenian|slovenscina)\b',
        # Ukrainian
        r'\b(ukr|uk|ukrainian|ukrainska)\b',
        # Lithuanian
        r'\b(lit|lt|lithuanian|lietuviu)\b',
        # Latvian
        r'\b(lav|lv|latvian|latviesu)\b',
        # Estonian
        r'\b(est|et|estonian|eesti)\b',
        # Thai
        r'\b(tha|th|thai)\b',
        # Vietnamese
        r'\b(vie|vi|vietnamese|tieng)\b',
        # Hindi
        r'\b(hin|hi|hindi)\b',
        # Bengali
        r'\b(ben|bn|bengali|bangla)\b',
        # Tamil
        r'\b(tam|ta|tamil)\b',
        # Telugu
        r'\b(tel|te|telugu)\b',
        # Marathi
        r'\b(mar|mr|marathi)\b',
        # Gujarati
        r'\b(guj|gu|gujarati)\b',
        # Punjabi
        r'\b(pan|pa|punjabi)\b',
        # Urdu
        r'\b(urd|ur|urdu)\b',
        # Persian/Farsi
        r'\b(per|fa|persian|farsi)\b',
        # Malay
        r'\b(may|ms|malay|bahasa)\b',
        # Indonesian
        r'\b(ind|id|indonesian|bahasa)\b',
        # Tagalog/Filipino
        r'\b(tgl|tl|tagalog|filipino)\b',
        # Swahili
        r'\b(swa|sw|swahili|kiswahili)\b',
        # Afrikaans
        r'\b(afr|af|afrikaans)\b',
        # Icelandic
        r'\b(ice|is|icelandic|islenska)\b',
        # Welsh
        r'\b(wel|cy|welsh|cymraeg)\b',
        # Irish
        r'\b(gle|ga|irish|gaeilge)\b',
        # Scottish Gaelic
        r'\b(gla|gd|gaelic|gaidhlig)\b',
        # Basque
        r'\b(baq|eu|basque|euskera)\b',
        # Catalan
        r'\b(cat|ca|catalan|catala)\b',
        # Galician
        r'\b(glg|gl|galician|galego)\b',
        # Maltese
        r'\b(mlt|mt|maltese|malti)\b',
        # Albanian
        r'\b(alb|sq|albanian|shqip)\b',
        # Macedonian
        r'\b(mac|mk|macedonian|makedonski)\b',
        # Bosnian
        r'\b(bos|bs|bosnian|bosanski)\b',
        # Montenegrin
        r'\b(cnr|me|montenegrin|crnogorski)\b',
        # Esperanto
        r'\b(epo|eo|esperanto)\b',
        # Latin
        r'\b(lat|la|latin|latina)\b',
        # Generic 2-letter and 3-letter codes
        r'\b([a-z]{2,3})\b'
    ]
    
    for pattern in lang_patterns:
        match = re.search(pattern, filename_lower)
        if match:
            info['language'] = match.group(1)
            break
    
    # Check for hearing impaired
    if re.search(r'\b(hearing[\s._-]?impaired|sdh|h\.i\.)\b', filename_lower) or re.search(r'\bHI\b', filename):
        info['hearing_impaired'] = True
    
    # Check for forced
    if re.search(r'\b(forced|foreign)\b', filename_lower):
        info['forced'] = True
    
    # Extract release group
    release_match = re.search(r'-([A-Z0-9]+)(?:\[.*\])?$', filename, re.IGNORECASE)
    if release_match:
        info['release_group'] = release_match.group(1)
    
    # Extract quality
    quality_patterns = [
        r'\b(720p|1080p|2160p|4k)\b',
        r'\b(bluray|bdrip|dvdrip|webrip|hdtv)\b'
    ]
    
    for pattern in quality_patterns:
        match = re.search(pattern, filename_lower)
        if match:
            info['quality'] = match.group(1)
            break
    
    return info