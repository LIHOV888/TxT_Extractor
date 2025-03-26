from flask import Flask, request, jsonify, render_template, send_file, after_this_request
import os
import logging
from processing import process_file
import atexit
import shutil
import time
import gzip
from datetime import datetime, timedelta

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024 * 1024 * 12  # 12GB max size to accommodate 10GB files
app.config['CHUNK_SIZE'] = 1024 * 1024 * 100  # 100MB chunks for processing
app.config['UPLOAD_FOLDER'] = 'temp'
app.config['ALLOWED_EXTENSIONS'] = {'txt', 'log', 'csv'}
app.config['MAX_FILE_AGE'] = 3600  # Maximum age of temp files in seconds (1 hour)

# Ensure temp directory exists
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def cleanup_temp_folder():
    """Clean up the temp folder on exit"""
    try:
        if os.path.exists(app.config['UPLOAD_FOLDER']):
            shutil.rmtree(app.config['UPLOAD_FOLDER'])
            os.makedirs(app.config['UPLOAD_FOLDER'])
    except Exception as e:
        logger.error(f"Cleanup error: {str(e)}")

# Register cleanup function
atexit.register(cleanup_temp_folder)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def get_file_stats(filepath):
    """Get file statistics"""
    stats = os.stat(filepath)
    return {
        'size': stats.st_size,
        'created': datetime.fromtimestamp(stats.st_ctime).strftime('%Y-%m-%d %H:%M:%S'),
        'modified': datetime.fromtimestamp(stats.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
    }

def cleanup_temp_files(exclude_file=None):
    """Clean up all files in temp folder except the specified file"""
    try:
        if os.path.exists(app.config['UPLOAD_FOLDER']):
            current_time = time.time()
            for file in os.listdir(app.config['UPLOAD_FOLDER']):
                if exclude_file and file == exclude_file:
                    continue
                    
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], file)
                try:
                    if os.path.isfile(file_path):
                        # Check if file is older than 5 minutes
                        if current_time - os.path.getctime(file_path) > 300:
                            os.unlink(file_path)
                            logger.info(f"Removed old file: {file_path}")
                except Exception as e:
                    logger.error(f"Error deleting {file_path}: {str(e)}")
    except Exception as e:
        logger.error(f"Cleanup error: {str(e)}")

@app.route('/cleanup', methods=['POST'])
def cleanup():
    """Cleanup endpoint to remove temp files"""
    try:
        data = request.json or {}
        exclude_file = data.get('exclude_file')
        
        # Only clean files older than 5 minutes, except current file
        current_time = time.time()
        if os.path.exists(app.config['UPLOAD_FOLDER']):
            for file in os.listdir(app.config['UPLOAD_FOLDER']):
                if exclude_file and file == exclude_file:
                    continue
                    
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], file)
                try:
                    if os.path.isfile(file_path):
                        file_age = current_time - os.path.getctime(file_path)
                        if file_age > 300:  # 5 minutes
                            os.unlink(file_path)
                            logger.info(f"Removed old file: {file_path} (age: {file_age:.1f}s)")
                except Exception as e:
                    logger.error(f"Error deleting {file_path}: {str(e)}")
                    
        return jsonify({'message': 'Cleanup successful'}), 200
    except Exception as e:
        logger.error(f"Cleanup error: {str(e)}")
        return jsonify({'error': str(e)}), 500

def validate_file_type(file_path):
    """Validate file type using extension"""
    try:
        file_ext = os.path.splitext(file_path)[1].lower()
        return file_ext in ['.txt', '.log', '.csv']
    except Exception as e:
        logger.error(f"File type validation error: {str(e)}")
        return False

@app.route('/')
def index():
    """Main page route"""
    cleanup_temp_files()  # Clean up on page load
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    """File upload route"""
    cleanup_temp_files()  # Clean up before new upload
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file part'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No selected file'}), 400
        
        if not allowed_file(file.filename):
            return jsonify({'error': 'File type not allowed'}), 400

        # Check file size before processing
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)
        
        if file_size > app.config['MAX_CONTENT_LENGTH']:
            return jsonify({'error': f'File too large. Maximum size is {app.config["MAX_CONTENT_LENGTH"] / (1024*1024*1024):.2f}GB'}), 400

        # Save file to temp directory using chunks with progress tracking
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
        chunk_size = app.config['CHUNK_SIZE']
        total_chunks = file_size // chunk_size + (1 if file_size % chunk_size else 0)
        chunks_processed = 0
        
        with open(filepath, 'wb') as f:
            while True:
                chunk = file.stream.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                chunks_processed += 1
                logger.info(f"Upload progress: {(chunks_processed/total_chunks)*100:.2f}%")
        
        # Validate file type
        if not validate_file_type(filepath):
            os.unlink(filepath)
            return jsonify({'error': 'Invalid file type detected'}), 400

        # Get file stats
        stats = get_file_stats(filepath)
        
        # Return initial chunk for preview (from the saved file)
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            initial_chunk = f.read(1024 * 10)  # Read first 10KB for preview
        
        return jsonify({
            'message': 'File uploaded successfully',
            'filename': file.filename,
            'preview': initial_chunk,
            'stats': stats
        })
    except Exception as e:
        logger.error(f"Upload error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/search', methods=['POST'])
def search():
    """Process file route"""
    cleanup_temp_files()  # Clean up before processing
    try:
        data = request.json
        filename = data.get('filename')
        remove_duplicates = data.get('removeDuplicates', True)
        validate_email = data.get('validateEmail', True)
        output_format = data.get('outputFormat', 'email')
        
        # Validate input
        if not filename:
            return jsonify({'error': 'No file specified'}), 400
            
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if not os.path.exists(filepath):
            logger.error(f"File not found: {filepath}")
            return jsonify({
                'error': 'File not found. Please upload the file again.',
                'results': [],
                'stats': {
                    'total_lines': 0,
                    'matches_found': 0,
                    'duplicates_removed': 0,
                    'processing_time': 0
                }
            }), 200  # Return 200 with empty results instead of 404

        # Get file stats for logging
        stats = get_file_stats(filepath)
        logger.info(f"Processing file: {filename} (size: {stats['size']} bytes)")

        try:
            # Process the file first
            result = process_file(filepath, {
                'remove_duplicates': remove_duplicates,
                'validate_email': validate_email,
                'output_format': output_format
            })

            if not result or 'error' in result:
                logger.error(f"Processing failed: {result.get('error') if result else 'Unknown error'}")
                return jsonify({'error': 'Processing failed. Please try again.'}), 500

            logger.info(f"Processing completed: {result.get('total_processed', 0)} items processed")
            
            # Get the result filename from processing result
            result_filename = result.get('result_file')
            if not result_filename:
                logger.error("No result file returned from processing")
                return jsonify({'error': 'Processing failed - no result file generated'}), 500
                
            result_filepath = os.path.join(app.config['UPLOAD_FOLDER'], result_filename)

            # Verify the result file exists
            if not os.path.exists(result_filepath):
                logger.error(f"Result file not found after processing: {result_filepath}")
                return jsonify({'error': 'Result file not found after processing'}), 500

            # Add processing stats to result
            result['stats'] = {
                'total_lines': result.get('stats', {}).get('total_lines', 0),
                'matches_found': result.get('total_results', 0),
                'duplicates_removed': result.get('stats', {}).get('duplicates_removed', 0),
                'processing_time': result.get('stats', {}).get('processing_time', 0)
            }
            return jsonify(result)
        except Exception as process_error:
            logger.error(f"Processing error: {str(process_error)}")
            return jsonify({'error': f'Processing error: {str(process_error)}'}), 500
        
    except Exception as e:
        logger.error(f"Search error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/download/<filename>')
def download(filename):
    """Download file route"""
    try:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        # Schedule cleanup after successful download
        @after_this_request
        def cleanup_after_download(response):
            try:
                if os.path.exists(filepath):
                    os.unlink(filepath)
                    logger.info(f"Cleaned up file after download: {filepath}")
            except Exception as e:
                logger.error(f"Cleanup error after download: {str(e)}")
            return response
        
        if not os.path.exists(filepath):
            return jsonify({'error': 'File not found'}), 404

        # Generate proper download name
        if '_email.txt' in filename:
            download_name = filename.replace('_email.txt', '_email_pass.txt')
        elif '_user.txt' in filename:
            download_name = filename.replace('_user.txt', '_user_pass.txt')
        else:
            download_name = filename

        logger.info(f"Serving file: {filepath} as {download_name}")

        try:
            # Create response
            response = send_file(
                filepath,
                as_attachment=True,
                download_name=download_name,
                mimetype='text/plain'
            )
            
            # Schedule cleanup after successful download
            @response.call_on_close
            def cleanup():
                try:
                    if os.path.exists(filepath):
                        os.remove(filepath)
                except Exception as e:
                    logger.error(f"Cleanup error after download: {str(e)}")
            
            return response
            
        except Exception as e:
            logger.error(f"Send file error: {str(e)}")
            if os.path.exists(filepath):
                os.remove(filepath)
            return jsonify({'error': 'Failed to send file'}), 500
    except Exception as e:
        logger.error(f"Download error: {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=8000)