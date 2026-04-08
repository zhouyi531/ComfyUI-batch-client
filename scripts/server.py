import os
import sys
import json
import base64
import io
import asyncio
import uuid
import time
from typing import Dict, Any, List, Optional
from urllib.parse import quote, unquote

from aiohttp import web

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)

from comfyuiclient.client import ComfyUIClientAsync
from comfyuiclient.workflow_manager import WorkflowManager
from PIL import Image

routes = web.RouteTableDef()

# Configuration
COMFY_SERVER = os.environ.get("COMFY_BASE_URL", "127.0.0.1:8188").replace("http://", "").replace("https://", "")

# Data directories
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
WORKFLOWS_DIR = os.path.join(DATA_DIR, "workflows")
TEMPLATES_DIR = os.path.join(DATA_DIR, "templates")
OUTPUTS_DIR = os.path.join(DATA_DIR, "outputs")

# Ensure directories exist
for d in [WORKFLOWS_DIR, TEMPLATES_DIR, OUTPUTS_DIR]:
    os.makedirs(d, exist_ok=True)

# Active batch jobs for cancellation
active_batch_jobs = {}  # job_id -> {"cancelled": bool, "results": [], "server": str}

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif'}
AUDIO_EXTENSIONS = {'.mp3', '.wav', '.flac', '.ogg', '.aac', '.m4a'}
VIDEO_EXTENSIONS = {'.mp4', '.webm', '.mov', '.avi', '.mkv'}

AUDIO_CONTENT_TYPES = {
    '.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.flac': 'audio/flac',
    '.ogg': 'audio/ogg', '.aac': 'audio/aac', '.m4a': 'audio/mp4',
}
VIDEO_CONTENT_TYPES = {
    '.mp4': 'video/mp4', '.webm': 'video/webm', '.mov': 'video/quicktime',
    '.avi': 'video/x-msvideo', '.mkv': 'video/x-matroska',
}

MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | AUDIO_EXTENSIONS | VIDEO_EXTENSIONS


def _resolved_existing_dir(path_str: str) -> Optional[str]:
    """Return realpath of directory if it exists, else None."""
    if not path_str or not path_str.strip():
        return None
    expanded = os.path.expanduser(path_str.strip())
    real = os.path.realpath(expanded)
    if not os.path.isdir(real):
        return None
    return real


def _is_path_under_root(root: str, filepath: str) -> bool:
    root_n = os.path.normcase(os.path.realpath(root))
    path_n = os.path.normcase(os.path.realpath(filepath))
    if path_n == root_n:
        return True
    prefix = root_n.rstrip(os.sep) + os.sep
    return path_n.startswith(prefix)


def _is_dir_under_anchor(anchor: str, dir_path: str) -> bool:
    """True if dir_path is anchor or a subdirectory of anchor (after realpath)."""
    a = os.path.normcase(os.path.realpath(anchor))
    d = os.path.normcase(os.path.realpath(dir_path))
    if d == a:
        return True
    prefix = a.rstrip(os.sep) + os.sep
    return d.startswith(prefix)


# ==================== Static Files ====================

@routes.get('/')
async def index(request):
    try:
        with open('web/index.html', 'r', encoding='utf-8') as f:
            return web.Response(text=f.read(), content_type='text/html')
    except FileNotFoundError:
        return web.Response(text="web/index.html not found", status=404)


# ==================== Workflow APIs ====================

@routes.get('/api/workflows')
async def list_workflows(request):
    """List all saved workflows"""
    workflows = []
    for f in os.listdir(WORKFLOWS_DIR):
        if f.endswith('.json'):
            name = f[:-5]
            workflows.append({"name": name, "filename": f})
    return web.json_response(workflows)

@routes.post('/api/workflows')
async def save_workflow(request):
    """Save a workflow"""
    try:
        data = await request.json()
        name = data.get('name', '').strip()
        workflow = data.get('workflow')
        
        if not name or not workflow:
            return web.Response(text="Missing name or workflow", status=400)
        
        # Sanitize filename
        safe_name = "".join(c for c in name if c.isalnum() or c in ('-', '_')).strip()
        if not safe_name:
            return web.Response(text="Invalid name", status=400)
        
        filepath = os.path.join(WORKFLOWS_DIR, f"{safe_name}.json")
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(workflow, f, indent=2)
        
        return web.json_response({"success": True, "name": safe_name})
    except Exception as e:
        return web.Response(text=str(e), status=400)

@routes.get('/api/workflows/{name}')
async def get_workflow(request):
    """Get a specific workflow"""
    name = request.match_info['name']
    filepath = os.path.join(WORKFLOWS_DIR, f"{name}.json")
    if not os.path.exists(filepath):
        return web.Response(text="Workflow not found", status=404)
    
    with open(filepath, 'r', encoding='utf-8') as f:
        workflow = json.load(f)
    return web.json_response(workflow)

@routes.delete('/api/workflows/{name}')
async def delete_workflow(request):
    """Delete a workflow"""
    name = request.match_info['name']
    filepath = os.path.join(WORKFLOWS_DIR, f"{name}.json")
    if os.path.exists(filepath):
        os.remove(filepath)
    return web.json_response({"success": True})


# ==================== Template APIs ====================

@routes.get('/api/templates')
async def list_templates(request):
    """List all saved templates"""
    templates = []
    for f in os.listdir(TEMPLATES_DIR):
        if f.endswith('.json'):
            name = f[:-5]
            templates.append({"name": name, "filename": f})
    return web.json_response(templates)

@routes.post('/api/templates')
async def save_template(request):
    """Save a template"""
    try:
        data = await request.json()
        name = data.get('name', '').strip()
        
        if not name:
            return web.Response(text="Missing name", status=400)
        
        safe_name = "".join(c for c in name if c.isalnum() or c in ('-', '_')).strip()
        if not safe_name:
            return web.Response(text="Invalid name", status=400)
        
        filepath = os.path.join(TEMPLATES_DIR, f"{safe_name}.json")
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        
        return web.json_response({"success": True, "name": safe_name})
    except Exception as e:
        return web.Response(text=str(e), status=400)

@routes.get('/api/templates/{name}')
async def get_template(request):
    """Get a specific template"""
    name = request.match_info['name']
    filepath = os.path.join(TEMPLATES_DIR, f"{name}.json")
    if not os.path.exists(filepath):
        return web.Response(text="Template not found", status=404)
    
    with open(filepath, 'r', encoding='utf-8') as f:
        template = json.load(f)
    return web.json_response(template)

@routes.put('/api/templates/{name}')
async def update_template(request):
    """Update a template"""
    name = request.match_info['name']
    filepath = os.path.join(TEMPLATES_DIR, f"{name}.json")
    
    try:
        data = await request.json()
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        return web.json_response({"success": True})
    except Exception as e:
        return web.Response(text=str(e), status=400)

@routes.delete('/api/templates/{name}')
async def delete_template(request):
    """Delete a template"""
    name = request.match_info['name']
    filepath = os.path.join(TEMPLATES_DIR, f"{name}.json")
    if os.path.exists(filepath):
        os.remove(filepath)
    return web.json_response({"success": True})


# ==================== File Upload API ====================

UPLOADS_DIR = os.path.join(DATA_DIR, "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)

@routes.post('/api/upload')
async def upload_file(request):
    """Upload a file to server for batch processing"""
    try:
        reader = await request.multipart()
        
        while True:
            part = await reader.next()
            if part is None:
                break
            
            if part.name == 'file':
                filename = part.filename or f"upload_{uuid.uuid4().hex[:8]}"
                # Get extension from original filename
                ext = os.path.splitext(filename)[1].lower() or '.png'
                # Generate safe ASCII filename to avoid encoding issues with ComfyUI
                safe_filename = f"upload_{int(time.time())}_{uuid.uuid4().hex[:6]}{ext}"
                
                filepath = os.path.join(UPLOADS_DIR, safe_filename)
                
                # Read all file data
                file_data = await part.read()
                total_bytes = len(file_data)
                
                # Save file
                with open(filepath, 'wb') as f:
                    f.write(file_data)
                
                # Validate image
                valid = False
                try:
                    img = Image.open(io.BytesIO(file_data))
                    img.verify()
                    valid = True
                    print(f"Uploaded file saved: {filepath} ({total_bytes} bytes, {img.format} {img.size if hasattr(img, 'size') else 'unknown'})")
                except Exception as e:
                    print(f"Uploaded file saved but invalid image: {filepath} ({total_bytes} bytes) - {e}")
                
                return web.json_response({
                    "success": True,
                    "filename": safe_filename,
                    "path": filepath,
                    "size": total_bytes,
                    "valid": valid
                })
        
        return web.Response(text="No file provided", status=400)
    except Exception as e:
        return web.Response(text=str(e), status=500)


# ==================== Server Status API ====================

@routes.get('/api/server/status')
async def server_status(request):
    """Check ComfyUI server connection status"""
    custom_server = request.query.get('server')
    server_addr = custom_server if custom_server else COMFY_SERVER
    
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            # Use /queue endpoint which is standard in ComfyUI
            async with session.get(f"http://{server_addr}/queue", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                # ComfyUI returns 200 for /queue even if queue is empty
                if resp.status == 200:
                    return web.json_response({"status": "connected", "server": server_addr})
                else:
                    return web.json_response({"status": "error", "server": server_addr, "code": resp.status})
    except asyncio.TimeoutError:
        return web.json_response({"status": "timeout", "server": server_addr})
    except Exception as e:
        return web.json_response({"status": "disconnected", "server": server_addr, "error": str(e)})


# ==================== Scanning API ====================

@routes.post('/api/scan')
async def scan(request):
    try:
        workflow = await request.json()
        inputs = WorkflowManager.scan_possible_inputs(workflow)
        return web.json_response(inputs)
    except Exception as e:
        return web.Response(text=str(e), status=400)


# ==================== Single Run API ====================

@routes.post('/api/run')
async def run(request):
    reader = await request.multipart()
    workflow_json = None
    inputs = {}
    custom_server = None
    
    while True:
        part = await reader.next()
        if part is None:
            break
        
        if part.name == 'workflow':
            raw = await part.read()
            workflow_json = json.loads(raw.decode('utf-8'))
        elif part.name == 'server_address':
             custom_server = await part.text()
        elif part.name.startswith('vars['):
            key = part.name[5:-1]
            val = await part.text()
            inputs[key] = val
        elif part.name.startswith('files['):
            key = part.name[6:-1]
            filename = part.filename or f"temp_{key}"
            file_data = await part.read()
            inputs[key] = {'filename': filename, 'data': file_data}
    
    if not workflow_json:
        return web.Response(text="Missing workflow", status=400)

    server_addr = custom_server if custom_server else COMFY_SERVER
    client = ComfyUIClientAsync(server_addr, "dummy.json")
    
    try:
        await client.connect()
        
        async def generate_from_workflow(wf):
            client.comfyui_prompt = wf
            return await client.generate()
        client.generate_from_workflow = generate_from_workflow

        processed_inputs = inputs.copy()
        for key, val in inputs.items():
            if isinstance(val, dict) and 'data' in val:
                print(f"Uploading {key}...")
                original_name = val['filename']
                ext = os.path.splitext(original_name)[1].lower() or '.png'
                safe_name = f"upload_{int(time.time())}_{uuid.uuid4().hex[:6]}{ext}"

                if ext in AUDIO_EXTENSIONS:
                    server_path = await client.upload_audio_bytes(val['data'], filename=safe_name)
                else:
                    server_path = await client.upload_image_bytes(val['data'], filename=safe_name)
                # ComfyUI nodes expect just the filename, not subfolder/filename
                if '/' in server_path:
                    server_path = server_path.split('/')[-1]
                processed_inputs[key] = server_path
                print(f"  -> Uploaded as: {server_path}")
        
        try:
            workflow_json = WorkflowManager.ensure_api_format(workflow_json)
        except ValueError as e:
            return web.Response(text=str(e), status=400)
        
        final_workflow = WorkflowManager.inject_variables(workflow_json, processed_inputs)
        
        print(f"Running workflow on {server_addr}...")
        results = await client.generate_from_workflow(final_workflow)
        
        resp_data = {}
        for node_id, data in results.items():
            if isinstance(data, Image.Image):
                buf = io.BytesIO()
                data.save(buf, format='PNG')
                b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
                resp_data[node_id] = {
                    'type': 'image',
                    'data': f'data:image/png;base64,{b64}'
                }
            elif isinstance(data, dict) and data.get('_type') == 'audio':
                ext = os.path.splitext(data['filename'])[1].lstrip('.') or 'flac'
                mime_types = {'mp3': 'audio/mpeg', 'wav': 'audio/wav', 'flac': 'audio/flac', 'ogg': 'audio/ogg', 'aac': 'audio/aac', 'm4a': 'audio/mp4'}
                mime = mime_types.get(ext, 'audio/flac')
                b64 = base64.b64encode(data['data']).decode('utf-8')
                resp_data[node_id] = {
                    'type': 'audio',
                    'filename': data['filename'],
                    'data': f'data:{mime};base64,{b64}'
                }
            else:
                resp_data[node_id] = {
                    'type': 'text',
                    'data': str(data)
                }

        return web.json_response(resp_data)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return web.Response(text=str(e), status=500)
    finally:
        await client.close()


# ==================== Batch Run API ====================

@routes.post('/api/batch')
async def batch_run(request):
    """Run batch jobs and return results"""
    try:
        data = await request.json()
        workflow = data.get('workflow')
        workflow_name = data.get('workflow_name', 'workflow')  # Get workflow name for output filenames
        batch_data = data.get('batch', [])  # List of input dicts
        custom_server = data.get('server_address')
        save_outputs = data.get('save_outputs', True)
        
        if not workflow or not batch_data:
            return web.Response(text="Missing workflow or batch data", status=400)
        
        # Ensure API format
        try:
            workflow = WorkflowManager.ensure_api_format(workflow)
        except ValueError as e:
            return web.Response(text=str(e), status=400)
        
        # Expand folder paths - if any value is a directory, expand to individual files
        expanded_batch = []
        
        for inputs in batch_data:
            folder_vars = {}
            
            # Find folder paths
            for key, value in inputs.items():
                if isinstance(value, str) and os.path.isdir(value):
                    files = []
                    for f in sorted(os.listdir(value)):
                        if os.path.splitext(f)[1].lower() in MEDIA_EXTENSIONS:
                            files.append(os.path.join(value, f))
                    if files:
                        folder_vars[key] = files
                        print(f"Expanding folder '{value}' -> {len(files)} media files")
            
            if not folder_vars:
                # No folders, keep as is
                expanded_batch.append(inputs)
            else:
                # Expand based on first folder variable
                primary_var = list(folder_vars.keys())[0]
                for i, file_path in enumerate(folder_vars[primary_var]):
                    new_inputs = inputs.copy()
                    new_inputs[primary_var] = file_path
                    # Handle other folder vars if any
                    for other_var, other_files in folder_vars.items():
                        if other_var != primary_var:
                            new_inputs[other_var] = other_files[i] if i < len(other_files) else other_files[-1]
                    expanded_batch.append(new_inputs)
        
        batch_data = expanded_batch
        print(f"Total jobs after folder expansion: {len(batch_data)}")
        
        server_addr = custom_server if custom_server else COMFY_SERVER
        client = ComfyUIClientAsync(server_addr, "dummy.json")
        
        job_id = f"batch_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        job_output_dir = os.path.join(OUTPUTS_DIR, job_id)
        os.makedirs(job_output_dir, exist_ok=True)
        
        # Register job for cancellation tracking
        active_batch_jobs[job_id] = {
            "cancelled": False,
            "results": [],
            "server": server_addr,
            "total": len(batch_data)
        }
        
        await client.connect()
        
        async def generate_from_workflow(wf):
            client.comfyui_prompt = wf
            return await client.generate()
        client.generate_from_workflow = generate_from_workflow
        
        cancelled = False
        try:
            for idx, inputs in enumerate(batch_data):
                # Check if cancelled
                if active_batch_jobs.get(job_id, {}).get("cancelled"):
                    print(f"Batch {job_id} cancelled at job {idx}")
                    cancelled = True
                    break
                
                print(f"Running batch job {idx+1}/{len(batch_data)}...")
                
                # Upload local files to ComfyUI server
                processed_inputs = {}
                for key, value in inputs.items():
                    if isinstance(value, str) and os.path.isfile(value):
                        # Local file exists, upload to ComfyUI
                        ext = os.path.splitext(value)[1].lower()
                        original_name = os.path.basename(value)
                        file_size = os.path.getsize(value)
                        safe_name = f"upload_{int(time.time())}_{uuid.uuid4().hex[:6]}{ext}"

                        if ext in IMAGE_EXTENSIONS:
                            print(f"  Uploading image {original_name} ({file_size} bytes)...")
                            with open(value, 'rb') as f:
                                file_data = f.read()
                            print(f"    Read {len(file_data)} bytes from file")
                            uploaded_path = await client.upload_image_bytes(file_data, filename=safe_name)
                            # ComfyUI LoadImage expects just the filename, not subfolder/filename
                            if '/' in uploaded_path:
                                uploaded_path = uploaded_path.split('/')[-1]
                            processed_inputs[key] = uploaded_path
                            print(f"    -> Uploaded as: {uploaded_path}")
                        elif ext in AUDIO_EXTENSIONS:
                            print(f"  Uploading audio {original_name} ({file_size} bytes)...")
                            with open(value, 'rb') as f:
                                file_data = f.read()
                            print(f"    Read {len(file_data)} bytes from file")
                            uploaded_path = await client.upload_audio_bytes(file_data, filename=safe_name)
                            if '/' in uploaded_path:
                                uploaded_path = uploaded_path.split('/')[-1]
                            processed_inputs[key] = uploaded_path
                            print(f"    -> Uploaded as: {uploaded_path}")
                        elif ext in VIDEO_EXTENSIONS:
                            print(f"  Uploading video {original_name} ({file_size} bytes)...")
                            with open(value, 'rb') as f:
                                file_data = f.read()
                            print(f"    Read {len(file_data)} bytes from file")
                            uploaded_path = await client.upload_image_bytes(file_data, filename=safe_name)
                            if '/' in uploaded_path:
                                uploaded_path = uploaded_path.split('/')[-1]
                            processed_inputs[key] = uploaded_path
                            print(f"    -> Uploaded as: {uploaded_path}")
                        else:
                            processed_inputs[key] = value
                    else:
                        processed_inputs[key] = value
                
                # Inject variables
                final_workflow = WorkflowManager.inject_variables(workflow, processed_inputs)
                
                # Debug: print injected values
                print(f"  Injected inputs: {processed_inputs}")
                
                # Debug: print the LoadImage node before and after injection
                if "7" in workflow:
                    print(f"  Node 7 BEFORE: {json.dumps(workflow['7'], indent=2)}")
                if "7" in final_workflow:
                    print(f"  Node 7 AFTER:  {json.dumps(final_workflow['7'], indent=2)}")
                
                # Run
                results = await client.generate_from_workflow(final_workflow)
                
                # Extract source image name from inputs for filename
                source_image_name = None
                for key, value in inputs.items():
                    if isinstance(value, str):
                        # Check if it's a file path
                        if os.path.isfile(value) or '/' in value or '\\' in value:
                            basename = os.path.basename(value)
                            name_without_ext = os.path.splitext(basename)[0]
                            source_image_name = name_without_ext
                            break
                
                if not source_image_name:
                    source_image_name = f"run_{idx}"
                
                # Sanitize workflow name for filename
                safe_workflow_name = "".join(c for c in workflow_name if c.isalnum() or c in ('-', '_')).strip()
                if not safe_workflow_name:
                    safe_workflow_name = "workflow"
                
                job_results = {"index": idx, "inputs": inputs, "outputs": []}
                
                for node_id, data in results.items():
                    if isinstance(data, Image.Image):
                        # Save to file with format: {original_image}_{workflow}.png
                        filename = f"{source_image_name}_{safe_workflow_name}.png"
                        filepath = os.path.join(job_output_dir, filename)
                        data.save(filepath, format='PNG')
                        job_results["outputs"].append({
                            "node_id": node_id,
                            "type": "image",
                            "filename": filename,
                            "url": f"/api/outputs/{job_id}/{filename}"
                        })
                    elif isinstance(data, dict) and data.get("_type") == "audio":
                        ext = os.path.splitext(data["filename"])[1] or ".flac"
                        filename = f"{source_image_name}_{safe_workflow_name}{ext}"
                        filepath = os.path.join(job_output_dir, filename)
                        with open(filepath, 'wb') as f:
                            f.write(data["data"])
                        job_results["outputs"].append({
                            "node_id": node_id,
                            "type": "audio",
                            "filename": filename,
                            "url": f"/api/outputs/{job_id}/{filename}"
                        })
                    else:
                        job_results["outputs"].append({
                            "node_id": node_id,
                            "type": "text",
                            "data": str(data)
                        })
                
                # Store result
                active_batch_jobs[job_id]["results"].append(job_results)
                print(f"  Job {idx+1} completed with {len(job_results['outputs'])} outputs")
        finally:
            await client.close()
            # Cleanup job tracking after a delay (keep for a while for status checks)
            asyncio.get_event_loop().call_later(300, lambda: active_batch_jobs.pop(job_id, None))
        
        results_to_return = active_batch_jobs.get(job_id, {}).get("results", [])
        completed = len(results_to_return)
        
        # Save parameters.json to the output directory
        parameters_info = {
            "job_id": job_id,
            "workflow_name": workflow_name,
            "server_address": server_addr,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total_jobs": len(batch_data),
            "completed_jobs": completed,
            "cancelled": cancelled,
            "batch_inputs": batch_data,
            "results": results_to_return
        }
        parameters_path = os.path.join(job_output_dir, "parameters.json")
        with open(parameters_path, 'w', encoding='utf-8') as f:
            json.dump(parameters_info, f, indent=2, ensure_ascii=False)
        print(f"Saved parameters to {parameters_path}")
        
        print(f"Batch {'cancelled' if cancelled else 'completed'}. {completed}/{len(batch_data)} jobs done.")
        return web.json_response({
            "job_id": job_id,
            "total": len(batch_data),
            "completed": completed,
            "cancelled": cancelled,
            "results": results_to_return
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return web.Response(text=str(e), status=500)


@routes.post('/api/batch/{job_id}/cancel')
async def cancel_batch(request):
    """Cancel a running batch job and interrupt ComfyUI"""
    job_id = request.match_info['job_id']
    
    if job_id not in active_batch_jobs:
        return web.json_response({"error": "Job not found or already completed"}, status=404)
    
    job = active_batch_jobs[job_id]
    job["cancelled"] = True
    
    # Send interrupt to ComfyUI
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.post(f"http://{job['server']}/interrupt") as resp:
                print(f"Sent interrupt to ComfyUI: {resp.status}")
    except Exception as e:
        print(f"Failed to send interrupt: {e}")
    
    return web.json_response({
        "success": True,
        "job_id": job_id,
        "completed": len(job["results"]),
        "results": job["results"]
    })


# ==================== Batch Parameters API ====================

@routes.get('/api/batch-parameters/{job_id}')
async def get_job_parameters(request):
    """Return the full parameters.json for a batch job (used by re-run)"""
    job_id = request.match_info['job_id']
    params_path = os.path.join(OUTPUTS_DIR, job_id, "parameters.json")
    if not os.path.exists(params_path):
        return web.Response(text="Parameters not found", status=404)
    with open(params_path, 'r', encoding='utf-8') as f:
        params = json.load(f)
    return web.json_response(params)


# ==================== Outputs API ====================

@routes.get('/api/outputs')
async def list_outputs(request):
    """List all output jobs with metadata from parameters.json"""
    jobs = []
    for d in sorted(os.listdir(OUTPUTS_DIR), reverse=True):
        job_dir = os.path.join(OUTPUTS_DIR, d)
        if os.path.isdir(job_dir):
            media_files = [f for f in os.listdir(job_dir)
                           if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS | AUDIO_EXTENSIONS]
            entry = {
                "job_id": d,
                "file_count": len(media_files),
            }
            params_path = os.path.join(job_dir, "parameters.json")
            if os.path.exists(params_path):
                try:
                    with open(params_path, 'r', encoding='utf-8') as f:
                        params = json.load(f)
                    entry["workflow_name"] = params.get("workflow_name", "")
                    entry["created_at"] = params.get("created_at", "")
                    entry["total_jobs"] = params.get("total_jobs", 0)
                    entry["completed_jobs"] = params.get("completed_jobs", 0)
                    entry["cancelled"] = params.get("cancelled", False)
                except Exception:
                    pass
            jobs.append(entry)
    return web.json_response(jobs)

@routes.get('/api/outputs/{job_id}')
async def get_outputs(request):
    """Get outputs for a specific job, with per-image parameters"""
    job_id = request.match_info['job_id']
    job_dir = os.path.join(OUTPUTS_DIR, job_id)
    
    if not os.path.exists(job_dir):
        return web.Response(text="Job not found", status=404)
    
    OUTPUT_EXTENSIONS = IMAGE_EXTENSIONS | AUDIO_EXTENSIONS

    # Build a map from filename -> input parameters using parameters.json
    file_params = {}
    params_meta = {}
    params_path = os.path.join(job_dir, "parameters.json")
    if os.path.exists(params_path):
        try:
            with open(params_path, 'r', encoding='utf-8') as f:
                params = json.load(f)
            params_meta = {
                "workflow_name": params.get("workflow_name", ""),
                "created_at": params.get("created_at", ""),
                "server_address": params.get("server_address", ""),
                "total_jobs": params.get("total_jobs", 0),
                "completed_jobs": params.get("completed_jobs", 0),
            }
            for result in params.get("results", []):
                inputs = result.get("inputs", {})
                for out in result.get("outputs", []):
                    fname = out.get("filename", "")
                    if fname:
                        file_params[fname] = inputs
        except Exception:
            pass

    files = []
    for f in sorted(os.listdir(job_dir)):
        ext = os.path.splitext(f)[1].lower()
        if ext in OUTPUT_EXTENSIONS:
            entry = {
                "filename": f,
                "url": f"/api/outputs/{job_id}/{f}",
                "type": "image" if ext in IMAGE_EXTENSIONS else "audio",
            }
            if f in file_params:
                entry["params"] = file_params[f]
            files.append(entry)
    
    return web.json_response({"job_id": job_id, "files": files, **params_meta})

@routes.delete('/api/outputs/{job_id}')
async def delete_job(request):
    """Delete an entire batch output directory"""
    import shutil
    job_id = request.match_info['job_id']
    job_dir = os.path.join(OUTPUTS_DIR, job_id)
    if not os.path.exists(job_dir):
        return web.Response(text="Job not found", status=404)
    shutil.rmtree(job_dir)
    return web.json_response({"success": True, "job_id": job_id})

@routes.delete('/api/outputs/{job_id}/{filename}')
async def delete_output_file(request):
    """Delete a single output file from a batch"""
    job_id = request.match_info['job_id']
    filename = request.match_info['filename']
    filepath = os.path.join(OUTPUTS_DIR, job_id, filename)
    if not os.path.exists(filepath):
        return web.Response(text="File not found", status=404)
    os.remove(filepath)
    params_path = os.path.join(OUTPUTS_DIR, job_id, "parameters.json")
    if os.path.exists(params_path):
        try:
            with open(params_path, 'r', encoding='utf-8') as f:
                params = json.load(f)
            for result in params.get("results", []):
                result["outputs"] = [
                    o for o in result.get("outputs", [])
                    if o.get("filename") != filename
                ]
            with open(params_path, 'w', encoding='utf-8') as f:
                json.dump(params, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
    return web.json_response({"success": True, "filename": filename})

@routes.get('/api/outputs/{job_id}/{filename}')
async def get_output_file(request):
    """Serve an output file (image or audio)"""
    job_id = request.match_info['job_id']
    filename = request.match_info['filename']
    filepath = os.path.join(OUTPUTS_DIR, job_id, filename)
    
    if not os.path.exists(filepath):
        return web.Response(text="File not found", status=404)
    
    ext = os.path.splitext(filename)[1].lower()
    if ext in AUDIO_CONTENT_TYPES:
        with open(filepath, 'rb') as f:
            data = f.read()
        return web.Response(
            body=data,
            content_type=AUDIO_CONTENT_TYPES[ext],
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    
    return web.FileResponse(filepath)


# ==================== Local folder browse (same machine as server) ====================

@routes.get('/api/local-folder/tree')
async def list_local_tree(request):
    """List immediate children of dir (folders + files); dir must be anchor or inside anchor."""
    raw_anchor = unquote(request.query.get('anchor', ''))
    raw_dir = unquote(request.query.get('dir', ''))
    anchor = _resolved_existing_dir(raw_anchor)
    dir_path = _resolved_existing_dir(raw_dir)
    if anchor is None or dir_path is None:
        return web.json_response({"error": "not_a_directory"}, status=400)
    if not _is_dir_under_anchor(anchor, dir_path):
        return web.json_response({"error": "outside_tree"}, status=400)

    try:
        names = os.listdir(dir_path)
    except OSError as e:
        return web.json_response({"error": str(e)}, status=400)

    def sort_key(name: str):
        full = os.path.join(dir_path, name)
        try:
            is_dir = os.path.isdir(full)
        except OSError:
            is_dir = False
        return (0 if is_dir else 1, name.lower())

    entries: List[Dict[str, Any]] = []
    for name in sorted(names, key=sort_key):
        full = os.path.join(dir_path, name)
        try:
            if os.path.isdir(full):
                real_d = os.path.realpath(full)
                if not _is_dir_under_anchor(anchor, real_d):
                    continue
                entries.append({"name": name, "kind": "dir", "path": real_d})
            elif os.path.isfile(full):
                entries.append({"name": name, "kind": "file"})
        except OSError:
            continue

    return web.json_response({
        "resolved_anchor": anchor,
        "resolved_dir": dir_path,
        "entries": entries,
    })


@routes.get('/api/local-folder')
async def list_local_folder(request):
    """List image/audio/video files in a directory on the server host."""
    raw = request.query.get('path', '')
    root = _resolved_existing_dir(unquote(raw))
    if root is None:
        return web.json_response({"error": "not_a_directory"}, status=400)

    files = []
    try:
        for f in sorted(os.listdir(root)):
            full = os.path.join(root, f)
            if not os.path.isfile(full):
                continue
            ext = os.path.splitext(f)[1].lower()
            if ext not in MEDIA_EXTENSIONS:
                continue
            qroot = quote(root, safe='')
            qname = quote(f, safe='')
            if ext in IMAGE_EXTENSIONS:
                ftype = "image"
            elif ext in AUDIO_EXTENSIONS:
                ftype = "audio"
            else:
                ftype = "video"
            entry = {
                "filename": f,
                "url": f"/api/local-folder/file?path={qroot}&name={qname}",
                "type": ftype,
                "path": full,
            }
            files.append(entry)
    except OSError as e:
        return web.json_response({"error": str(e)}, status=400)

    return web.json_response({
        "files": files,
        "resolved_path": root,
        "local": True,
    })


@routes.get('/api/local-folder/file')
async def get_local_folder_file(request):
    """Serve one file from a directory previously opened via /api/local-folder."""
    raw_root = unquote(request.query.get('path', ''))
    name = unquote(request.query.get('name', ''))
    root = _resolved_existing_dir(raw_root)
    if root is None or not name or os.path.basename(name) != name:
        return web.Response(text="Bad request", status=400)
    filepath = os.path.join(root, name)
    if not _is_path_under_root(root, filepath) or not os.path.isfile(filepath):
        return web.Response(text="Not found", status=404)

    ext = os.path.splitext(name)[1].lower()
    ct = AUDIO_CONTENT_TYPES.get(ext) or VIDEO_CONTENT_TYPES.get(ext)
    if ct:
        with open(filepath, 'rb') as f:
            data = f.read()
        return web.Response(
            body=data,
            content_type=ct,
            headers={"Content-Disposition": f'inline; filename="{name}"'}
        )
    return web.FileResponse(filepath)


# ==================== App Setup ====================

app = web.Application()
app.add_routes(routes)

if __name__ == '__main__':
    print(f"Data directory: {DATA_DIR}")
    print(f"Starting server at http://127.0.0.1:8000")
    web.run_app(app, port=8000)
