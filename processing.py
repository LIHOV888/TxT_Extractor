import re
import time
import logging
from typing import Dict, Any
import os

logger = logging.getLogger(__name__)

def is_valid_email(email: str) -> bool:
    """Validate email format using regex."""
    email_pattern = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
    return bool(email_pattern.match(email))

def process_file(filepath: str, options: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process large files efficiently using chunked processing and streaming approach.
    
    Args:
        filepath: Path to the file to process.
        options: Processing options:
            - remove_duplicates: bool
            - validate_email: bool
            - output_format: str ('email' or 'user')
            
    Returns:
        Dictionary containing results, stats, and result file info.
    """
    BUFFER_SIZE = 1024 * 1024 * 10  # 10MB buffer for reading
    start_time = time.time()
    stats = {
        'total_lines': 0,
        'matches_found': 0,
        'duplicates_removed': 0,
        'invalid_lines': 0,
        'processing_time': 0
    }

    # Regex patterns to match different credential formats
    patterns = [
        re.compile(r'https?://[^:]+:([^:]+):(.+)$'),  # URL:user:pass
        re.compile(r'^([^:|\t]+):([^:|\t]+)$'),        # user:pass
        re.compile(r'^([^|]+)\|(.+)$'),                # user|pass
        re.compile(r'^([^\t]+)\t(.+)$')                # user\tpass
    ]

    # Create result filename
    result_filename = f"result_{os.path.splitext(os.path.basename(filepath))[0]}_{options['output_format']}.txt"
    result_path = os.path.join(os.path.dirname(filepath), result_filename)
    
    try:
        # Process file in streaming fashion
        seen = set() if options.get('remove_duplicates', False) else None
        buffer = []
        buffer_size = 0
        
        def flush_buffer():
            nonlocal buffer, buffer_size
            if buffer:
                with open(result_path, 'a', encoding='utf-8') as out:
                    out.write(''.join(buffer))
                buffer = []
                buffer_size = 0

        def process_line(line: str) -> str:
            """Process a single line and return formatted credential if valid."""
            line = line.strip()
            for pattern in patterns:
                match = pattern.match(line)
                if match:
                    identifier = match.group(1).strip()
                    password = match.group(2).strip()
                    
                    # Determine if identifier is an email
                    if options.get('validate_email', False):
                        is_email = is_valid_email(identifier)
                    else:
                        is_email = '@' in identifier and '.' in identifier.split('@', 1)[-1]
                    
                    # Validate based on output format
                    output_format = options.get('output_format', 'email')
                    if output_format == 'email' and not is_email:
                        return None
                    elif output_format == 'user' and is_email:
                        return None
                    
                    return f"{identifier}:{password}\n"
            return None

        # Create new result file
        open(result_path, 'w').close()

        # Process the file in chunks
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as file:
            for line in file:
                stats['total_lines'] += 1
                result = process_line(line)
                
                if result:
                    if seen is not None:  # Remove duplicates
                        if result not in seen:
                            seen.add(result)
                            buffer.append(result)
                            stats['matches_found'] += 1
                            buffer_size += len(result)
                        else:
                            stats['duplicates_removed'] += 1
                    else:  # Keep duplicates
                        buffer.append(result)
                        stats['matches_found'] += 1
                        buffer_size += len(result)
                else:
                    stats['invalid_lines'] += 1

                # Flush buffer when it reaches the size limit
                if buffer_size >= BUFFER_SIZE:
                    flush_buffer()

            # Final flush
            flush_buffer()

        # Get total results count
        total_results = sum(1 for _ in open(result_path, 'r', encoding='utf-8'))
        
        stats['processing_time'] = round(time.time() - start_time, 2)
        
        return {
            'results': [],  # Don't return results in memory for large files
            'total_results': total_results,
            'stats': stats,
            'result_file': result_filename
        }
    
    except Exception as e:
        logger.error(f"Processing error: {str(e)}")
        stats['processing_time'] = round(time.time() - start_time, 2)
        return {
            'results': [],
            'total_results': 0,
            'stats': stats,
            'result_file': None
        }