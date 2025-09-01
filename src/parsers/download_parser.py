"""Parser for OpenSubtitles download functionality"""

import logging
import re
import zipfile
import io
from typing import Optional, Dict, Any, List
from bs4 import BeautifulSoup

from ..utils.exceptions import DownloadError, ParseError
from ..utils.helpers import sanitize_filename

logger = logging.getLogger(__name__)


class DownloadParser:
    """Parser for OpenSubtitles download functionality"""
    
    def __init__(self):
        self.base_url = "https://www.opensubtitles.org"
    
    def extract_download_link(self, html_content: str, subtitle_id: str) -> Optional[str]:
        """Extract actual download link from download page"""
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Look for OpenSubtitles-specific download patterns first
            # Pattern 1: https://dl.opensubtitles.org/en/download/sub/ID
            dl_link = soup.find('a', href=re.compile(r'dl\.opensubtitles\.org/en/download/sub/\d+'))
            if dl_link:
                url = dl_link['href']
                logger.debug(f"Found dl.opensubtitles.org download link: {url}")
                return url
            
            # Pattern 2: https://dl.opensubtitles.org/en/download/file/ID
            file_link = soup.find('a', href=re.compile(r'dl\.opensubtitles\.org/en/download/file/\d+'))
            if file_link:
                url = file_link['href']
                logger.debug(f"Found direct file download link: {url}")
                return url
            
            # Pattern 3: /en/subtitleserve/sub/ID (from JavaScript)
            scripts = soup.find_all('script')
            for script in scripts:
                if script.string:
                    # Look for subtitleserve links in JavaScript
                    serve_match = re.search(r'/en/subtitleserve/sub/(\d+)', script.string)
                    if serve_match:
                        url = self.base_url + serve_match.group(0)
                        logger.debug(f"Found subtitleserve link: {url}")
                        return url
            
            # Pattern 4: Look for any download links with subtitle ID
            download_links = soup.find_all('a', href=re.compile(r'download'))
            for link in download_links:
                href = link.get('href', '')
                if subtitle_id in href:
                    if href.startswith('/'):
                        href = self.base_url + href
                    logger.debug(f"Found download link with subtitle ID: {href}")
                    return href
            
            # Fallback: Look for direct download link
            download_link = soup.find('a', href=re.compile(r'\.zip$|download.*\.zip'))
            if download_link:
                url = download_link['href']
                if url.startswith('/'):
                    url = self.base_url + url
                logger.debug(f"Found generic download link: {url}")
                return url
            
            # Look for form-based download
            download_form = soup.find('form', action=re.compile(r'download'))
            if download_form:
                action = download_form['action']
                if action.startswith('/'):
                    action = self.base_url + action
                logger.debug(f"Found download form: {action}")
                return action
            
            # Look for meta refresh or redirect
            meta_refresh = soup.find('meta', attrs={'http-equiv': 'refresh'})
            if meta_refresh and meta_refresh.get('content'):
                content = meta_refresh['content']
                url_match = re.search(r'url=([^;]+)', content)
                if url_match:
                    url = url_match.group(1)
                    if url.startswith('/'):
                        url = self.base_url + url
                    logger.debug(f"Found meta refresh link: {url}")
                    return url
            
            logger.warning(f"Could not find download link for subtitle {subtitle_id}")
            return None
            
        except Exception as e:
            logger.error(f"Failed to extract download link: {e}")
            raise ParseError(f"Download link extraction failed: {e}")
    
    def parse_download_page(self, html_content: str) -> Dict[str, Any]:
        """Parse download page to extract metadata and download info"""
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            
            info = {
                'filename': None,
                'size': None,
                'format': None,
                'encoding': None,
                'download_url': None,
                'requires_captcha': False,
                'wait_time': 0
            }
            
            # Extract filename
            filename_elem = soup.find(text=re.compile(r'\.srt|\.sub|\.ass'))
            if filename_elem:
                filename_match = re.search(r'([^/\\]+\.(srt|sub|ass|vtt))', str(filename_elem))
                if filename_match:
                    info['filename'] = sanitize_filename(filename_match.group(1))
            
            # Extract file size
            size_elem = soup.find(text=re.compile(r'\d+\s*(KB|MB|bytes)', re.I))
            if size_elem:
                size_match = re.search(r'(\d+(?:\.\d+)?)\s*(KB|MB|bytes)', str(size_elem), re.I)
                if size_match:
                    size_value = float(size_match.group(1))
                    size_unit = size_match.group(2).upper()
                    if size_unit == 'KB':
                        info['size'] = int(size_value * 1024)
                    elif size_unit == 'MB':
                        info['size'] = int(size_value * 1024 * 1024)
                    else:
                        info['size'] = int(size_value)
            
            # Check for CAPTCHA
            captcha_elem = soup.find(['img', 'div'], class_=re.compile(r'captcha', re.I))
            if captcha_elem:
                info['requires_captcha'] = True
            
            # Check for wait time
            wait_elem = soup.find(text=re.compile(r'wait.*\d+.*second', re.I))
            if wait_elem:
                wait_match = re.search(r'(\d+)', str(wait_elem))
                if wait_match:
                    info['wait_time'] = int(wait_match.group(1))
            
            # Extract download URL
            info['download_url'] = self.extract_download_link(html_content, "unknown")
            
            return info
            
        except Exception as e:
            logger.error(f"Failed to parse download page: {e}")
            raise ParseError(f"Download page parsing failed: {e}")
    
    def extract_subtitle_from_zip(self, zip_content: bytes, preferred_filename: Optional[str] = None) -> Dict[str, Any]:
        """Extract subtitle file from ZIP archive"""
        try:
            subtitle_files = []
            
            with zipfile.ZipFile(io.BytesIO(zip_content), 'r') as zip_file:
                for file_info in zip_file.filelist:
                    filename = file_info.filename
                    
                    # Skip directories and non-subtitle files
                    if file_info.is_dir():
                        continue
                    
                    # Check if it's a subtitle file
                    if not re.search(r'\.(srt|sub|ass|vtt|ssa)$', filename, re.I):
                        continue
                    
                    # Read file content
                    try:
                        file_content = zip_file.read(filename)
                        
                        # Try to decode with common encodings
                        content_text = self._decode_subtitle_content(file_content)
                        
                        subtitle_files.append({
                            'filename': sanitize_filename(filename),
                            'content': content_text,
                            'size': len(file_content),
                            'encoding': self._detect_encoding(file_content)
                        })
                        
                    except Exception as e:
                        logger.warning(f"Failed to read file {filename} from ZIP: {e}")
                        continue
            
            if not subtitle_files:
                raise DownloadError("No subtitle files found in ZIP archive")
            
            # Select the best subtitle file
            selected_subtitle = self._select_best_subtitle(subtitle_files, preferred_filename)
            
            logger.info(f"Extracted subtitle: {selected_subtitle['filename']}")
            return selected_subtitle
            
        except zipfile.BadZipFile:
            raise DownloadError("Invalid ZIP file")
        except Exception as e:
            logger.error(f"Failed to extract subtitle from ZIP: {e}")
            raise DownloadError(f"ZIP extraction failed: {e}")
    
    def _decode_subtitle_content(self, content: bytes) -> str:
        """Decode subtitle content with multiple encoding attempts"""
        encodings = ['utf-8', 'utf-8-sig', 'latin1', 'cp1252', 'iso-8859-1', 'windows-1252']
        
        for encoding in encodings:
            try:
                decoded_content = content.decode(encoding)
                # Normalize line endings for better compatibility
                normalized_content = self._normalize_subtitle_content(decoded_content)
                return normalized_content
            except UnicodeDecodeError:
                continue
        
        # Fallback: decode with errors='replace'
        logger.warning("Could not decode subtitle with standard encodings, using fallback")
        decoded_content = content.decode('utf-8', errors='replace')
        return self._normalize_subtitle_content(decoded_content)
    
    def _normalize_subtitle_content(self, content: str) -> str:
        """Normalize subtitle content for better parser compatibility"""
        try:
            # Normalize line endings to Unix format (\n)
            content = content.replace('\r\n', '\n').replace('\r', '\n')
            
            # Remove BOM if present
            if content.startswith('\ufeff'):
                content = content[1:]
            
            # Ensure proper spacing between subtitle blocks
            lines = content.split('\n')
            normalized_lines = []
            
            for i, line in enumerate(lines):
                line = line.strip()
                if line:
                    normalized_lines.append(line)
                elif i > 0 and normalized_lines and normalized_lines[-1]:
                    # Add empty line only if previous line wasn't empty
                    normalized_lines.append('')
            
            # Ensure file ends with newline
            result = '\n'.join(normalized_lines)
            if result and not result.endswith('\n'):
                result += '\n'
            
            logger.debug("Normalized subtitle content for better parser compatibility")
            return result
            
        except Exception as e:
            logger.warning(f"Failed to normalize subtitle content: {e}")
            return content
    
    def _detect_encoding(self, content: bytes) -> str:
        """Detect encoding of subtitle content"""
        # Check for BOM
        if content.startswith(b'\xef\xbb\xbf'):
            return 'utf-8-sig'
        elif content.startswith(b'\xff\xfe'):
            return 'utf-16-le'
        elif content.startswith(b'\xfe\xff'):
            return 'utf-16-be'
        
        # Try to decode with common encodings
        encodings = ['utf-8', 'latin1', 'cp1252', 'iso-8859-1']
        
        for encoding in encodings:
            try:
                content.decode(encoding)
                return encoding
            except UnicodeDecodeError:
                continue
        
        return 'utf-8'  # Default fallback
    
    def _select_best_subtitle(self, subtitle_files: List[Dict[str, Any]], 
                            preferred_filename: Optional[str] = None) -> Dict[str, Any]:
        """Select the best subtitle file from multiple options"""
        if len(subtitle_files) == 1:
            return subtitle_files[0]
        
        # If preferred filename is specified, try to match it
        if preferred_filename:
            preferred_name = preferred_filename.lower()
            for subtitle in subtitle_files:
                if preferred_name in subtitle['filename'].lower():
                    return subtitle
        
        # Prefer SRT files
        srt_files = [s for s in subtitle_files if s['filename'].lower().endswith('.srt')]
        if srt_files:
            # Return the largest SRT file (usually better quality)
            return max(srt_files, key=lambda x: x['size'])
        
        # Fallback: return the largest file
        return max(subtitle_files, key=lambda x: x['size'])
    
    def validate_subtitle_content(self, content: str) -> bool:
        """Validate that content is a valid subtitle file"""
        try:
            # Check for common subtitle patterns
            lines = content.strip().split('\n')
            
            # SRT format validation
            if re.search(r'^\d+$', lines[0].strip()) and '-->' in content:
                return True
            
            # ASS/SSA format validation
            if '[Script Info]' in content or '[V4+ Styles]' in content:
                return True
            
            # VTT format validation
            if content.startswith('WEBVTT'):
                return True
            
            # Generic subtitle validation - look for timestamps
            if re.search(r'\d{2}:\d{2}:\d{2}', content):
                return True
            
            return False
            
        except Exception:
            return False