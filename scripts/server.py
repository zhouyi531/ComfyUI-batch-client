import os
import sys
import json
import base64
import io
import asyncio
import uuid
import time
from typing import Dict, Any, List

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
                server_path = await client.upload_image_bytes(val['data'], filename=val['filename'])
                processed_inputs[key] = server_path
        
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
        IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif'}
        expanded_batch = []
        
        for inputs in batch_data:
            folder_vars = {}
            
            # Find folder paths
            for key, value in inputs.items():
                if isinstance(value, str) and os.path.isdir(value):
                    # List image files in folder
                    files = []
                    for f in sorted(os.listdir(value)):
                        if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS:
                            files.append(os.path.join(value, f))
                    if files:
                        folder_vars[key] = files
                        print(f"Expanding folder '{value}' -> {len(files)} images")
            
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
                        if ext in IMAGE_EXTENSIONS:
                            print(f"  Uploading {os.path.basename(value)}...")
                            with open(value, 'rb') as f:
                                file_data = f.read()
                            uploaded_path = await client.upload_image_bytes(file_data, filename=os.path.basename(value))
                            processed_inputs[key] = uploaded_path
                        else:
                            processed_inputs[key] = value
                    else:
                        processed_inputs[key] = value
                
                # Inject variables
                final_workflow = WorkflowManager.inject_variables(workflow, processed_inputs)
                
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
                        
                        # Return URL instead of base64 (much smaller response)
                        job_results["outputs"].append({
                            "node_id": node_id,
                            "type": "image",
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


# ==================== Outputs API ====================

@routes.get('/api/outputs')
async def list_outputs(request):
    """List all output jobs"""
    jobs = []
    for d in sorted(os.listdir(OUTPUTS_DIR), reverse=True):
        job_dir = os.path.join(OUTPUTS_DIR, d)
        if os.path.isdir(job_dir):
            files = [f for f in os.listdir(job_dir) if f.endswith('.png')]
            jobs.append({
                "job_id": d,
                "file_count": len(files)
            })
    return web.json_response(jobs)

@routes.get('/api/outputs/{job_id}')
async def get_outputs(request):
    """Get outputs for a specific job"""
    job_id = request.match_info['job_id']
    job_dir = os.path.join(OUTPUTS_DIR, job_id)
    
    if not os.path.exists(job_dir):
        return web.Response(text="Job not found", status=404)
    
    files = []
    for f in sorted(os.listdir(job_dir)):
        if f.endswith('.png'):
            files.append({
                "filename": f,
                "url": f"/api/outputs/{job_id}/{f}"
            })
    
    return web.json_response({"job_id": job_id, "files": files})

@routes.get('/api/outputs/{job_id}/{filename}')
async def get_output_file(request):
    """Serve an output image file"""
    job_id = request.match_info['job_id']
    filename = request.match_info['filename']
    filepath = os.path.join(OUTPUTS_DIR, job_id, filename)
    
    if not os.path.exists(filepath):
        return web.Response(text="File not found", status=404)
    
    return web.FileResponse(filepath)


# ==================== App Setup ====================

app = web.Application()
app.add_routes(routes)

if __name__ == '__main__':
    print(f"Data directory: {DATA_DIR}")
    print(f"Starting server at http://127.0.0.1:8000")
    web.run_app(app, port=8000)
