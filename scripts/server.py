import os
import sys
import json
import base64
import io
import asyncio
import uuid
import time
import random
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
APIS_DIR = os.path.join(DATA_DIR, "apis")

# Ensure directories exist
for d in [WORKFLOWS_DIR, TEMPLATES_DIR, OUTPUTS_DIR, APIS_DIR]:
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
RANDOM_SEED_MARKER = '__random_seed__'
COMFY_SEED_MAX = 1125899906842624

def resolve_random_seeds(inputs: dict) -> dict:
    """Replace __random_seed__ markers with random values in ComfyUI's seed range."""
    resolved = {}
    for key, value in inputs.items():
        if isinstance(value, str) and value == RANDOM_SEED_MARKER:
            resolved[key] = str(random.randint(0, COMFY_SEED_MAX))
        else:
            resolved[key] = value
    return resolved

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
    workflows.sort(key=lambda w: w["name"])
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
    templates.sort(key=lambda t: t["name"])
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
        
        processed_inputs = resolve_random_seeds(processed_inputs)
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
                
                # Resolve random seed markers with unique values per job
                processed_inputs = resolve_random_seeds(processed_inputs)
                
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
                
                job_results = {"index": idx, "inputs": processed_inputs, "outputs": []}
                
                for node_id, data in results.items():
                    if isinstance(data, Image.Image):
                        filename = f"{source_image_name}_{safe_workflow_name}_{idx}.png"
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
                        filename = f"{source_image_name}_{safe_workflow_name}_{idx}{ext}"
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


# ==================== PDF Export ====================

_PDF_FONTS = None


def _register_pdf_fonts():
    """Register fonts for PDF export once. Returns (latin, latin_bold, cjk)."""
    global _PDF_FONTS
    if _PDF_FONTS is not None:
        return _PDF_FONTS

    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    latin, latin_bold, cjk = 'Helvetica', 'Helvetica-Bold', None

    sans_candidates = [
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
        '/Library/Fonts/Arial.ttf',
    ]
    bold_candidates = [
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
        '/Library/Fonts/Arial Bold.ttf',
    ]
    try:
        for p in sans_candidates:
            if os.path.isfile(p):
                pdfmetrics.registerFont(TTFont('PDFSans', p))
                latin = 'PDFSans'
                break
        for p in bold_candidates:
            if os.path.isfile(p):
                pdfmetrics.registerFont(TTFont('PDFSans-Bold', p))
                latin_bold = 'PDFSans-Bold'
                break
    except Exception as e:
        print(f"PDF latin font registration failed: {e}")

    # CID font ships with reportlab and renders CJK reliably without a TTF file.
    try:
        pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
        cjk = 'STSong-Light'
    except Exception as e:
        print(f"PDF CJK font registration failed: {e}")

    _PDF_FONTS = (latin, latin_bold, cjk)
    return _PDF_FONTS


def _pdf_pick_font(text, latin, cjk):
    """Use the CJK font when the string has non-ASCII characters."""
    if not cjk:
        return latin
    try:
        str(text).encode('ascii')
        return latin
    except UnicodeEncodeError:
        return cjk


def _pdf_wrap(text, font, size, max_w):
    """Wrap text to max_w; handles spaceless CJK by breaking on characters."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    text = str(text).replace('\r', ' ').replace('\t', '    ')
    lines = []
    for paragraph in text.split('\n'):
        if not paragraph:
            lines.append('')
            continue
        cur = ''
        for word in paragraph.split(' '):
            trial = word if not cur else cur + ' ' + word
            if stringWidth(trial, font, size) <= max_w:
                cur = trial
                continue
            if cur:
                lines.append(cur)
                cur = ''
            if stringWidth(word, font, size) <= max_w:
                cur = word
            else:
                chunk = ''
                for ch in word:
                    if stringWidth(chunk + ch, font, size) <= max_w:
                        chunk += ch
                    else:
                        if chunk:
                            lines.append(chunk)
                        chunk = ch
                cur = chunk
        lines.append(cur)
    return lines


def _pdf_image_reader(img_path, max_px=1500, quality=85):
    """Open an image, downscale, and return a JPEG-backed ImageReader plus size."""
    from reportlab.lib.utils import ImageReader
    with Image.open(img_path) as im:
        im = im.convert('RGB')
        im.thumbnail((max_px, max_px), Image.LANCZOS)
        w, h = im.size
        b = io.BytesIO()
        im.save(b, format='JPEG', quality=quality)
        b.seek(0)
    return ImageReader(b), w, h


def build_batch_pdf(meta, entries):
    """
    Build a polished multi-page PDF report.

    meta:    {"title", "subtitle", "info": [(label, value), ...]}
    entries: [{"path", "filename", "params": {..}|None}, ...]  (images only)
    """
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase.pdfmetrics import stringWidth

    latin, latin_bold, cjk = _register_pdf_fonts()

    ACCENT = (0.541, 0.361, 0.965)   # #8a5cf6
    ACCENT_DK = (0.345, 0.31, 0.78)  # indigo
    INK = (0.094, 0.106, 0.149)      # near-black slate
    SUB = (0.42, 0.45, 0.53)
    HAIR = (0.886, 0.894, 0.929)
    PANEL = (0.965, 0.967, 0.98)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    M = 44.0
    content_w = W - 2 * M

    def draw_text(x, y, s, size, rgb=INK, bold=False):
        base = latin_bold if bold else latin
        c.setFont(_pdf_pick_font(s, base, cjk), size)
        c.setFillColorRGB(*rgb)
        c.drawString(x, y, s)

    def draw_centered(x, y, s, size, rgb=INK, bold=False):
        base = latin_bold if bold else latin
        c.setFont(_pdf_pick_font(s, base, cjk), size)
        c.setFillColorRGB(*rgb)
        c.drawCentredString(x, y, s)

    images = [e for e in entries if e.get('path')]
    total = len(images)

    # ---------------- Cover page ----------------
    header_h = 196
    c.setFillColorRGB(*ACCENT_DK)
    c.roundRect(M, H - M - header_h, content_w, header_h, 20, fill=1, stroke=0)
    c.setFillColorRGB(*ACCENT)
    c.roundRect(M, H - M - header_h, content_w, header_h - 8, 20, fill=1, stroke=0)

    tx = M + 30
    ty = H - M - 52
    draw_text(tx, ty, 'BATCH REPORT', 11, (1, 1, 1), bold=True)
    title = meta.get('title') or 'ComfyUI Batch'
    t_size = 30
    while stringWidth(title, _pdf_pick_font(title, latin_bold, cjk), t_size) > content_w - 60 and t_size > 16:
        t_size -= 1
    draw_text(tx, ty - 42, title, t_size, (1, 1, 1), bold=True)
    if meta.get('subtitle'):
        draw_text(tx, ty - 70, meta['subtitle'], 12, (0.93, 0.92, 1))

    badge = f"{total} image{'s' if total != 1 else ''}"
    bw = stringWidth(badge, latin_bold, 11) + 24
    c.setFillColorRGB(1, 1, 1)
    c.setFillAlpha(0.18)
    c.roundRect(M + content_w - bw - 26, H - M - 50, bw, 26, 13, fill=1, stroke=0)
    c.setFillAlpha(1)
    draw_centered(M + content_w - bw / 2 - 26, H - M - 43, badge, 11, (1, 1, 1), bold=True)

    info = meta.get('info') or []
    iy = H - M - header_h - 42
    for label, value in info:
        draw_text(M + 4, iy, str(label).upper(), 9, ACCENT, bold=True)
        for ln in _pdf_wrap(value, latin, 11.5, content_w - 150):
            draw_text(M + 150, iy, ln, 11.5, INK)
            iy -= 16
        iy -= 8
        c.setStrokeColorRGB(*HAIR)
        c.setLineWidth(0.6)
        c.line(M + 4, iy + 6, M + content_w - 4, iy + 6)
        iy -= 12

    # Thumbnail strip on the cover (first images)
    if images:
        grid = images[:4]
        gap = 14
        cell = (content_w - gap * (len(grid) - 1)) / len(grid) if len(grid) else content_w
        cell = min(cell, 150)
        gy = M + 70
        gx = M
        draw_text(M + 4, gy + cell + 14, 'PREVIEW', 9, SUB, bold=True)
        for e in grid:
            try:
                reader, iw, ih = _pdf_image_reader(e['path'], max_px=420, quality=80)
                scale = min(cell / iw, cell / ih)
                dw, dh = iw * scale, ih * scale
                c.setFillColorRGB(*PANEL)
                c.roundRect(gx, gy, cell, cell, 10, fill=1, stroke=0)
                c.drawImage(reader, gx + (cell - dw) / 2, gy + (cell - dh) / 2,
                            width=dw, height=dh, mask='auto')
            except Exception:
                pass
            gx += cell + gap

    draw_centered(W / 2, M + 24, 'Generated by ComfyUI Studio', 9, SUB)
    c.showPage()

    # ---------------- Contact sheet (thumbnail grid) ----------------
    def ellipsize(s, font, size, max_w):
        if stringWidth(s, font, size) <= max_w:
            return s
        out = s
        while out and stringWidth(out + '…', font, size) > max_w:
            out = out[:-1]
        return out + '…'

    if images:
        cols, rows = 3, 4
        per_page = cols * rows
        gap = 16
        header_h = 30
        footer_h = 26
        caption_h = 15
        cell_pad = 8

        top = H - M
        grid_top = top - header_h
        grid_bottom = M + footer_h
        cell_w = (content_w - gap * (cols - 1)) / cols
        cell_h = (grid_top - grid_bottom - gap * (rows - 1)) / rows
        img_area_h = cell_h - caption_h

        total_pages = (len(images) + per_page - 1) // per_page
        for page_idx in range(total_pages):
            # Header
            draw_text(M, top - 18, meta.get('title') or 'Batch', 13, INK, bold=True)
            badge = f"{total} images"
            draw_text(M + content_w - stringWidth(badge, latin, 9.5) - 2, top - 17, badge, 9.5, SUB)

            # Footer
            c.setStrokeColorRGB(*HAIR)
            c.setLineWidth(0.6)
            c.line(M, M + 18, W - M, M + 18)
            draw_text(M, M + 5, 'Generated by ComfyUI Studio', 8.5, SUB)
            draw_centered(W - M - 24, M + 5, f"{page_idx + 1} / {total_pages}", 8.5, SUB)

            chunk = images[page_idx * per_page: page_idx * per_page + per_page]
            for i, e in enumerate(chunk):
                r, col = divmod(i, cols)
                cx = M + col * (cell_w + gap)
                cy = grid_top - (r + 1) * cell_h - r * gap  # bottom-left of cell

                c.setFillColorRGB(*PANEL)
                c.roundRect(cx, cy, cell_w, cell_h, 9, fill=1, stroke=0)

                try:
                    reader, iw, ih = _pdf_image_reader(e['path'], max_px=620, quality=80)
                    box_w = cell_w - cell_pad * 2
                    box_h = img_area_h - cell_pad * 2
                    scale = min(box_w / iw, box_h / ih)
                    dw, dh = iw * scale, ih * scale
                    ix = cx + (cell_w - dw) / 2
                    iy = cy + caption_h + (img_area_h - dh) / 2
                    c.drawImage(reader, ix, iy, width=dw, height=dh, mask='auto')
                except Exception:
                    pass

                fname = e.get('filename') or ''
                cap_font = _pdf_pick_font(fname, latin, cjk)
                disp = ellipsize(fname, cap_font, 7.5, cell_w - 12)
                c.setFont(cap_font, 7.5)
                c.setFillColorRGB(*SUB)
                c.drawCentredString(cx + cell_w / 2, cy + 5, disp)

            c.showPage()
    else:
        draw_centered(W / 2, H / 2, 'No images to export.', 14, SUB)
        c.showPage()

    c.save()
    return buf.getvalue()


def _pdf_response(pdf_bytes, base_name):
    import re as _re
    import urllib.parse as _up
    safe = _re.sub(r'[^A-Za-z0-9._-]+', '_', base_name).strip('_') or 'report'
    ascii_name = f"{safe}.pdf"
    utf8_name = _up.quote(f"{base_name}.pdf")
    return web.Response(
        body=pdf_bytes,
        content_type='application/pdf',
        headers={
            'Content-Disposition': f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}"
        },
    )


@routes.get('/api/pdf/job/{job_id}')
async def export_job_pdf(request):
    """Render all images of a batch job into a downloadable PDF report."""
    job_id = request.match_info['job_id']
    job_dir = os.path.join(OUTPUTS_DIR, job_id)
    if not os.path.isdir(job_dir):
        return web.Response(text="Job not found", status=404)

    meta = {"title": job_id, "subtitle": job_id, "info": []}
    file_params = {}
    params_path = os.path.join(job_dir, "parameters.json")
    if os.path.exists(params_path):
        try:
            with open(params_path, 'r', encoding='utf-8') as f:
                params = json.load(f)
            meta["title"] = params.get("workflow_name") or job_id
            meta["subtitle"] = job_id
            meta["info"] = [
                ("Workflow", params.get("workflow_name", "—")),
                ("Created", params.get("created_at", "—")),
                ("Server", params.get("server_address", "—")),
                ("Jobs", f"{params.get('completed_jobs', '?')} / {params.get('total_jobs', '?')} completed"),
            ]
            for result in params.get("results", []):
                inputs = result.get("inputs", {})
                for out in result.get("outputs", []):
                    fn = out.get("filename", "")
                    if fn:
                        file_params[fn] = inputs
        except Exception:
            pass

    entries = []
    for f in sorted(os.listdir(job_dir)):
        if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS:
            entries.append({
                "path": os.path.join(job_dir, f),
                "filename": f,
                "params": file_params.get(f),
            })

    try:
        loop = asyncio.get_event_loop()
        pdf_bytes = await loop.run_in_executor(None, build_batch_pdf, meta, entries)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return web.Response(text=f"PDF generation failed: {e}", status=500)

    return _pdf_response(pdf_bytes, meta["title"] or job_id)


@routes.get('/api/pdf/local')
async def export_local_folder_pdf(request):
    """Render all images of a local folder into a downloadable PDF report."""
    root = _resolved_existing_dir(unquote(request.query.get('path', '')))
    if root is None:
        return web.json_response({"error": "not_a_directory"}, status=400)

    name = os.path.basename(root.rstrip(os.sep)) or 'folder'
    entries = []
    for f in sorted(os.listdir(root)):
        full = os.path.join(root, f)
        if os.path.isfile(full) and os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS:
            entries.append({"path": full, "filename": f, "params": None})

    meta = {
        "title": name,
        "subtitle": root,
        "info": [
            ("Folder", root),
            ("Images", str(len(entries))),
            ("Created", time.strftime("%Y-%m-%d %H:%M:%S")),
        ],
    }

    try:
        loop = asyncio.get_event_loop()
        pdf_bytes = await loop.run_in_executor(None, build_batch_pdf, meta, entries)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return web.Response(text=f"PDF generation failed: {e}", status=500)

    return _pdf_response(pdf_bytes, name)


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


# ==================== Saved APIs ====================

API_FILE_KEYWORDS = ['image', 'video', 'audio', 'file', 'path', 'media', 'source']


def _api_safe_name(name: str) -> str:
    return "".join(c for c in str(name) if c.isalnum() or c in ('-', '_')).strip()


def _guess_param_type(field, alias, base_type):
    name = f"{field or ''} {alias or ''}".lower()
    if any(k in name for k in API_FILE_KEYWORDS):
        return 'image'
    if base_type == 'number':
        return 'number'
    if base_type == 'boolean':
        return 'boolean'
    return 'text'


def _scan_output_nodes(workflow):
    """Best-effort list of output-producing nodes for documentation."""
    try:
        wf = WorkflowManager.ensure_api_format(workflow)
    except Exception:
        return []
    out = []
    markers = ('SaveImage', 'PreviewImage', 'SaveAudio', 'PreviewAudio',
               'VHS_VideoCombine', 'SaveVideo', 'SaveAnimated', 'Save')
    for nid, node in wf.items():
        if not isinstance(node, dict):
            continue
        ct = node.get('class_type', '') or ''
        if any(m in ct for m in markers):
            out.append({"node_id": nid, "class_type": ct})
    return out


def _load_api(name):
    safe = _api_safe_name(name)
    if not safe:
        return None
    fp = os.path.join(APIS_DIR, f"{safe}.json")
    if not os.path.exists(fp):
        return None
    with open(fp, 'r', encoding='utf-8') as f:
        return json.load(f)


def _load_live_workflow(workflow_name):
    """Load the current workflow JSON from data/workflows/ by name.

    Used so /api/v1 always runs the up-to-date workflow instead of the snapshot
    frozen when the API was saved. Returns None if the file is missing.
    """
    safe = _api_safe_name(workflow_name or '')
    if not safe:
        return None
    fp = os.path.join(WORKFLOWS_DIR, f"{safe}.json")
    if not os.path.exists(fp):
        return None
    with open(fp, 'r', encoding='utf-8') as f:
        return json.load(f)


@routes.get('/api/apis')
async def list_apis(request):
    """List all saved APIs (lightweight metadata)."""
    items = []
    for f in os.listdir(APIS_DIR):
        if not f.endswith('.json'):
            continue
        try:
            with open(os.path.join(APIS_DIR, f), 'r', encoding='utf-8') as fh:
                d = json.load(fh)
        except Exception:
            continue
        items.append({
            "id": d.get('id', f[:-5]),
            "name": d.get('name', f[:-5]),
            "workflow_name": d.get('workflow_name', ''),
            "param_count": len(d.get('params', [])),
            "created_at": d.get('created_at', ''),
        })
    items.sort(key=lambda x: x.get('created_at', ''), reverse=True)
    return web.json_response(items)


@routes.post('/api/apis')
async def create_api(request):
    """Create (upsert) an API from a workflow snapshot + parameter variables."""
    import re as _re
    try:
        data = await request.json()
    except Exception:
        return web.Response(text="Invalid JSON", status=400)

    name = (data.get('name') or '').strip()
    workflow = data.get('workflow')
    variables = data.get('variables') or []

    if not name:
        return web.Response(text="Missing API name", status=400)
    if not workflow:
        return web.Response(text="Missing workflow", status=400)

    safe = _api_safe_name(name)
    if not safe:
        return web.Response(text="Invalid API name (use letters, numbers, - or _)", status=400)

    params = []
    seen = set()
    for v in variables:
        base = (v.get('alias') or v.get('field') or v.get('id') or 'param')
        pub = _re.sub(r'\s+', '_', str(base).strip()) or 'param'
        cand = pub
        i = 2
        while cand in seen:
            cand = f"{pub}_{i}"
            i += 1
        seen.add(cand)
        ptype = v.get('param_type') or _guess_param_type(v.get('field'), v.get('alias'), v.get('type'))
        params.append({
            "name": cand,
            "id": v.get('id'),
            "node_id": v.get('node_id'),
            "field": v.get('field'),
            "alias": v.get('alias'),
            "param_type": ptype,
            "type": v.get('type', 'text'),
            "default": v.get('default', ''),
            "required": bool(ptype == 'image'),
            "random_seed": bool(v.get('random_seed')),
        })

    api_def = {
        "name": name,
        "id": safe,
        "workflow_name": data.get('workflow_name') or name,
        "server_address": (data.get('server_address') or '').replace("http://", "").replace("https://", ""),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "params": params,
        "output_nodes": _scan_output_nodes(workflow),
        "workflow": workflow,
    }

    with open(os.path.join(APIS_DIR, f"{safe}.json"), 'w', encoding='utf-8') as f:
        json.dump(api_def, f, indent=2, ensure_ascii=False)

    return web.json_response({"success": True, "id": safe, "name": name})


@routes.get('/api/apis/{name}')
async def get_api(request):
    """Return an API definition (without the heavy workflow snapshot)."""
    d = _load_api(request.match_info['name'])
    if d is None:
        return web.Response(text="API not found", status=404)
    out = {k: v for k, v in d.items() if k != 'workflow'}
    out['endpoint_path'] = f"/api/v1/{d.get('id')}"
    return web.json_response(out)


def _coerce_default(ptype, default):
    if default in (None, ''):
        return None
    if ptype == 'number':
        try:
            s = str(default)
            return float(s) if ('.' in s or 'e' in s.lower()) else int(s)
        except (ValueError, TypeError):
            return default
    if ptype == 'boolean':
        return str(default).strip().lower() in ('1', 'true', 'yes', 'on')
    return default


def _build_openapi(api_def, base_url):
    """Build an OpenAPI 3.1 spec for a saved API so AI/codegen tools can generate a client."""
    api_id = api_def.get('id')
    name = api_def.get('name', api_id)
    wf_name = api_def.get('workflow_name', name)
    params = api_def.get('params', [])

    props = {}
    required = []
    for p in params:
        ptype = p.get('param_type', 'text')
        if ptype == 'image':
            schema = {
                "type": "string",
                "format": "uri",
                "description": "Public image URL (http/https). The server downloads it before running; local file paths are not accepted.",
            }
        elif ptype == 'number':
            schema = {"type": "number"}
        elif ptype == 'boolean':
            schema = {"type": "boolean"}
        else:
            schema = {"type": "string"}

        if ptype != 'image':
            dv = _coerce_default(ptype, p.get('default'))
            if dv is not None:
                schema['default'] = dv
        if p.get('random_seed'):
            schema['description'] = (schema.get('description', '') + " Omit to auto-randomize.").strip()

        props[p['name']] = schema
        if p.get('required'):
            required.append(p['name'])

    request_schema = {"type": "object", "properties": props}
    if required:
        request_schema['required'] = required

    output_item = {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["image", "audio", "text"], "description": "Output kind."},
            "filename": {"type": "string", "description": "Saved file name (image/audio)."},
            "mime_type": {"type": "string", "description": "MIME type of the base64 payload, e.g. image/png (image outputs)."},
            "base64": {"type": "string", "contentEncoding": "base64", "description": "Base64-encoded image bytes (image outputs). Decode this to get the image."},
            "url": {"type": "string", "description": "Hosted URL of the output file (image/audio)."},
            "data": {"type": "string", "description": "Raw text — present for text outputs only."},
        },
    }
    success_schema = {
        "type": "object",
        "properties": {
            "job_id": {"type": "string", "description": "Output folder id (also visible in the Browse tab)."},
            "outputs": {"type": "array", "items": output_item},
        },
        "required": ["job_id", "outputs"],
    }
    error_schema = {"type": "object", "properties": {"error": {"type": "string"}}, "required": ["error"]}

    return {
        "openapi": "3.1.0",
        "info": {
            "title": f"{name} — ComfyUI API",
            "version": "1.0.0",
            "description": (
                f"ComfyUI workflow '{wf_name}' exposed as an HTTP API by ComfyUI Studio.\n\n"
                "Image inputs must be public URLs (http/https); the server downloads them before running. "
                "Local file paths are not accepted."
            ),
        },
        "servers": [{"url": base_url or "/"}],
        "paths": {
            f"/api/v1/{api_id}": {
                "post": {
                    "operationId": f"run_{api_id}",
                    "summary": f"Run {name}",
                    "description": f"Runs the '{wf_name}' workflow with the given parameters and returns the generated outputs.",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": request_schema}},
                    },
                    "responses": {
                        "200": {
                            "description": "Success — workflow ran and produced outputs.",
                            "content": {"application/json": {"schema": success_schema}},
                        },
                        "400": {
                            "description": "Invalid request — missing/invalid image URL (must be http/https), or body is not a JSON object.",
                            "content": {"application/json": {"schema": error_schema}},
                        },
                        "404": {
                            "description": "API not found.",
                            "content": {"application/json": {"schema": error_schema}},
                        },
                        "500": {
                            "description": "Execution error — ComfyUI unreachable, image download failed, or the workflow raised an error.",
                            "content": {"application/json": {"schema": error_schema}},
                        },
                    },
                }
            }
        },
        "components": {
            "schemas": {
                f"{api_id}_Request": request_schema,
                f"{api_id}_Response": success_schema,
                "Error": error_schema,
            }
        },
    }


@routes.get('/api/apis/{name}/openapi.json')
async def get_api_openapi(request):
    """OpenAPI 3.1 spec for a saved API — paste this (or its URL) into any AI/codegen tool."""
    d = _load_api(request.match_info['name'])
    if d is None:
        return web.json_response({"error": "API not found"}, status=404)
    try:
        base_url = str(request.url.origin())
    except Exception:
        base_url = ''
    return web.json_response(_build_openapi(d, base_url))


@routes.delete('/api/apis/{name}')
async def delete_api(request):
    safe = _api_safe_name(request.match_info['name'])
    fp = os.path.join(APIS_DIR, f"{safe}.json")
    if os.path.exists(fp):
        os.remove(fp)
    return web.json_response({"success": True})


async def _download_to_bytes(url, max_bytes=128 * 1024 * 1024):
    """Download a remote file into memory (used for API image inputs)."""
    import aiohttp
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise ValueError(f"Failed to download '{url}' (HTTP {resp.status})")
            data = await resp.read()
            if len(data) > max_bytes:
                raise ValueError("Downloaded file is too large")
            return data


def _ext_from_url(url):
    from urllib.parse import urlparse
    ext = os.path.splitext(urlparse(url).path)[1].lower()
    if ext not in MEDIA_EXTENSIONS:
        return '.png'
    return ext


@routes.post('/api/v1/{name}')
async def call_api(request):
    """Public callable endpoint for a saved API. Image params must be URLs."""
    api_def = _load_api(request.match_info['name'])
    if api_def is None:
        return web.json_response({"error": "API not found"}, status=404)

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return web.json_response({"error": "Request body must be a JSON object"}, status=400)

    # Prefer the live workflow from data/workflows/ so later edits take effect
    # without re-saving the API; fall back to the frozen snapshot if the file
    # was deleted or renamed.
    live_workflow = _load_live_workflow(api_def.get('workflow_name'))
    workflow = live_workflow if live_workflow is not None else api_def.get('workflow')
    if not workflow:
        return web.json_response({"error": "API has no workflow"}, status=400)
    print(f"API '{api_def['id']}': using {'live' if live_workflow is not None else 'snapshot'} workflow")

    params = api_def.get('params', [])
    server_addr = (body.get('_server') or api_def.get('server_address') or COMFY_SERVER)
    server_addr = server_addr.replace("http://", "").replace("https://", "")

    # Build inputs keyed by node.field id
    inputs = {}
    image_urls = {}
    for p in params:
        pub, pid, ptype = p['name'], p['id'], p.get('param_type', 'text')
        provided = pub in body
        if ptype == 'image':
            url = body.get(pub, '')
            if not url:
                if p.get('required'):
                    return web.json_response({"error": f"Missing required image URL parameter '{pub}'"}, status=400)
                continue
            if not str(url).startswith(('http://', 'https://')):
                return web.json_response(
                    {"error": f"Image parameter '{pub}' must be a public URL (http/https). Local files are not supported."},
                    status=400)
            image_urls[pid] = str(url)
        elif p.get('random_seed') and not provided:
            inputs[pid] = RANDOM_SEED_MARKER
        else:
            inputs[pid] = body.get(pub, p.get('default', ''))

    try:
        workflow = WorkflowManager.ensure_api_format(workflow)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)

    client = ComfyUIClientAsync(server_addr, "dummy.json")
    job_id = f"api_{api_def['id']}_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    job_dir = os.path.join(OUTPUTS_DIR, job_id)

    try:
        await client.connect()

        async def generate_from_workflow(wf):
            client.comfyui_prompt = wf
            return await client.generate()
        client.generate_from_workflow = generate_from_workflow

        # Download remote inputs and upload them to ComfyUI
        for pid, url in image_urls.items():
            print(f"API '{api_def['id']}': downloading {url}")
            file_data = await _download_to_bytes(url)
            ext = _ext_from_url(url)
            safe_name = f"upload_{int(time.time())}_{uuid.uuid4().hex[:6]}{ext}"
            if ext in AUDIO_EXTENSIONS:
                server_path = await client.upload_audio_bytes(file_data, filename=safe_name)
            else:
                server_path = await client.upload_image_bytes(file_data, filename=safe_name)
            if '/' in server_path:
                server_path = server_path.split('/')[-1]
            inputs[pid] = server_path
            print(f"  -> uploaded as {server_path}")

        inputs = resolve_random_seeds(inputs)
        final_workflow = WorkflowManager.inject_variables(workflow, inputs)

        print(f"API '{api_def['id']}' running on {server_addr}...")
        results = await client.generate_from_workflow(final_workflow)

        os.makedirs(job_dir, exist_ok=True)
        try:
            base_url = str(request.url.origin())
        except Exception:
            base_url = ''

        outputs = []
        saved_outputs = []
        idx = 0
        for node_id, rdata in results.items():
            if isinstance(rdata, Image.Image):
                filename = f"{api_def['id']}_{int(time.time())}_{idx}.png"
                rdata.save(os.path.join(job_dir, filename), format='PNG')
                buf = io.BytesIO()
                rdata.save(buf, format='PNG')
                b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
                rel = f"/api/outputs/{job_id}/{filename}"
                outputs.append({
                    "type": "image",
                    "filename": filename,
                    "mime_type": "image/png",
                    "base64": b64,
                    "url": (base_url + rel) if base_url else rel,
                })
                saved_outputs.append({"node_id": node_id, "type": "image", "filename": filename, "url": rel})
                idx += 1
            elif isinstance(rdata, dict) and rdata.get("_type") == "audio":
                ext = os.path.splitext(rdata["filename"])[1] or ".flac"
                filename = f"{api_def['id']}_{int(time.time())}_{idx}{ext}"
                with open(os.path.join(job_dir, filename), 'wb') as f:
                    f.write(rdata["data"])
                rel = f"/api/outputs/{job_id}/{filename}"
                outputs.append({
                    "type": "audio",
                    "filename": filename,
                    "url": (base_url + rel) if base_url else rel,
                })
                saved_outputs.append({"node_id": node_id, "type": "audio", "filename": filename, "url": rel})
                idx += 1
            else:
                outputs.append({"type": "text", "data": str(rdata)})

        if not outputs:
            return web.json_response(
                {"error": "Workflow finished but produced no outputs"}, status=502)

        # Persist a parameters.json so API runs also show up in the Browse tab
        parameters_info = {
            "job_id": job_id,
            "workflow_name": f"API: {api_def.get('name', api_def['id'])}",
            "server_address": server_addr,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total_jobs": 1,
            "completed_jobs": 1,
            "cancelled": False,
            "results": [{"index": 0, "inputs": inputs, "outputs": saved_outputs}],
        }
        with open(os.path.join(job_dir, "parameters.json"), 'w', encoding='utf-8') as f:
            json.dump(parameters_info, f, indent=2, ensure_ascii=False)

        return web.json_response({"job_id": job_id, "outputs": outputs})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return web.json_response({"error": str(e)}, status=500)
    finally:
        await client.close()


# ==================== App Setup ====================

app = web.Application()
app.add_routes(routes)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", "8931"))
    print(f"Data directory: {DATA_DIR}")
    print(f"Starting server at http://127.0.0.1:{port}")
    web.run_app(app, port=port)
