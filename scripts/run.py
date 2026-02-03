import argparse
import asyncio
import json
import os
import sys
import glob
from typing import List, Dict, Any

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from comfyuiclient.client import ComfyUIClientAsync
from comfyuiclient.workflow_manager import WorkflowManager

# Supported file extensions for folder expansion
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif'}
VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mov', '.webm', '.mkv'}
AUDIO_EXTENSIONS = {'.mp3', '.wav', '.flac', '.ogg', '.aac'}


def expand_folder_inputs(inputs: Dict[str, Any], var_types: Dict[str, str]) -> List[Dict[str, Any]]:
    """
    If any input value is a folder path, expand it to multiple entries.
    Returns a list of input dicts (one per file combination).
    """
    folder_vars = []
    file_lists = {}
    
    for key, value in inputs.items():
        if isinstance(value, str) and os.path.isdir(value):
            # Determine what extensions to look for
            var_type = var_types.get(key, 'file')
            if var_type == 'image' or key.lower().find('image') >= 0:
                extensions = IMAGE_EXTENSIONS
            elif var_type == 'video' or key.lower().find('video') >= 0:
                extensions = VIDEO_EXTENSIONS
            elif var_type == 'audio' or key.lower().find('audio') >= 0:
                extensions = AUDIO_EXTENSIONS
            else:
                extensions = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS
            
            # List files in folder
            files = []
            for f in sorted(os.listdir(value)):
                if os.path.splitext(f)[1].lower() in extensions:
                    files.append(os.path.join(value, f))
            
            if files:
                folder_vars.append(key)
                file_lists[key] = files
                print(f"Found {len(files)} files in folder '{value}' for variable '{key}'")
    
    if not folder_vars:
        return [inputs]
    
    # For simplicity, iterate through the first folder's files only
    # More complex: could do cartesian product of all folders
    primary_var = folder_vars[0]
    expanded = []
    
    for file_path in file_lists[primary_var]:
        new_inputs = inputs.copy()
        new_inputs[primary_var] = file_path
        
        # If there are other folder vars, use corresponding index or repeat
        for other_var in folder_vars[1:]:
            idx = file_lists[primary_var].index(file_path)
            if idx < len(file_lists[other_var]):
                new_inputs[other_var] = file_lists[other_var][idx]
            else:
                new_inputs[other_var] = file_lists[other_var][-1]  # Repeat last
        
        expanded.append(new_inputs)
    
    return expanded


async def extract_vars(args):
    try:
        with open(args.workflow, 'r', encoding='utf-8') as f:
            workflow = json.load(f)
    except Exception as e:
        print(f"Error reading workflow file: {e}")
        return

    vars = WorkflowManager.extract_vars(workflow) if hasattr(WorkflowManager, 'extract_vars') else WorkflowManager.extract_variables(workflow)
    print(json.dumps(vars, indent=2))


async def run_workflow(client: ComfyUIClientAsync, workflow: Dict, inputs: Dict[str, Any], output_dir: str, var_types: Dict[str, str]):
    # Process inputs: upload images if needed
    processed_inputs = inputs.copy()
    
    for key, value in inputs.items():
        # Check if it's a file type variable and local file exists
        var_type = var_types.get(key, '')
        is_file_type = var_type in ['image', 'video', 'audio', 'file'] or key.lower().find('image') >= 0
        
        if is_file_type and isinstance(value, str) and os.path.exists(value) and os.path.isfile(value):
            print(f"Uploading {key}: {value}...")
            with open(value, 'rb') as f:
                file_data = f.read()
                filename = os.path.basename(value)
                server_path = await client.upload_image_bytes(file_data, filename=filename)
                processed_inputs[key] = server_path
                print(f"Uploaded to {server_path}")

    # Inject variables
    final_workflow = WorkflowManager.inject_variables(workflow, processed_inputs)

    # Queue prompt
    print("Queueing workflow...")
    results = await client.generate_from_workflow(final_workflow)
    
    # Save results
    os.makedirs(output_dir, exist_ok=True)
    for node_id, data in results.items():
        from PIL import Image
        if isinstance(data, Image.Image):
            filename = f"{output_dir}/output_{node_id}.png"
            data.save(filename)
            print(f"Saved {filename}")
        else:
            filename = f"{output_dir}/output_{node_id}.txt"
            with open(filename, "w", encoding='utf-8') as f:
                f.write(str(data))
            print(f"Saved {filename}")


async def run(args):
    workflow = None
    var_types = {}
    
    # Load from template or workflow
    if args.template:
        try:
            with open(args.template, 'r', encoding='utf-8') as f:
                template = json.load(f)
                workflow = template.get('workflow')
                # Build var_types from template variables
                for v in template.get('variables', []):
                    var_types[v['id']] = v.get('type', 'text')
                print(f"Loaded template with {len(template.get('variables', []))} variables")
        except Exception as e:
            print(f"Error loading template: {e}")
            return
    else:
        try:
            with open(args.workflow, 'r', encoding='utf-8') as f:
                workflow = json.load(f)
        except Exception as e:
            print(f"Error loading workflow: {e}")
            return
        
        # Extract var types from regex variables
        vars_def = WorkflowManager.extract_variables(workflow)
        var_types = {v['name']: v['type'] for v in vars_def}

    if not workflow:
        print("Error: No workflow found")
        return

    # Ensure API format
    try:
        workflow = WorkflowManager.ensure_api_format(workflow)
    except ValueError as e:
        print(f"Error: {e}")
        return

    # Collect inputs
    inputs_list = []

    if args.batch:
        try:
            with open(args.batch, 'r', encoding='utf-8') as f:
                batch_data = json.load(f)
                if isinstance(batch_data, list):
                    inputs_list = batch_data
                elif isinstance(batch_data, dict):
                    inputs_list = [batch_data]
        except Exception as e:
            print(f"Error loading batch file: {e}")
            return
    else:
        # Single run from CLI args
        current_inputs = {}
        if args.set:
            for item in args.set:
                k, v = item.split('=', 1)
                current_inputs[k] = v
        if args.file:
            for item in args.file:
                k, v = item.split('=', 1)
                current_inputs[k] = v
        inputs_list.append(current_inputs)

    # Expand folder paths
    expanded_inputs = []
    for inputs in inputs_list:
        expanded = expand_folder_inputs(inputs, var_types)
        expanded_inputs.extend(expanded)
    inputs_list = expanded_inputs
    
    print(f"Total jobs to run: {len(inputs_list)}")

    # Initialize client
    server_addr = os.environ.get("COMFY_BASE_URL", "127.0.0.1:8188").replace("http://", "").replace("https://", "")
    client = ComfyUIClientAsync(server_addr, "dummy.json")
    
    async def generate_from_workflow(wf):
        client.comfyui_prompt = wf
        return await client.generate()
    
    client.generate_from_workflow = generate_from_workflow

    await client.connect()
    
    try:
        for i, inputs in enumerate(inputs_list):
            print(f"\n=== Running job {i+1}/{len(inputs_list)} ===")
            sub_output_dir = os.path.join(args.out, f"run_{i}") if len(inputs_list) > 1 else args.out
            await run_workflow(client, workflow, inputs, sub_output_dir, var_types)
    finally:
        await client.close()
    
    print(f"\n✅ Completed {len(inputs_list)} jobs. Output saved to: {args.out}")


def main():
    parser = argparse.ArgumentParser(description="ComfyUI Client Builder CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # extract-vars
    parser_extract = subparsers.add_parser("extract-vars", help="Extract variables from workflow")
    parser_extract.add_argument("workflow", help="Path to workflow.json")
    parser_extract.set_defaults(func=extract_vars)

    # run
    parser_run = subparsers.add_parser("run", help="Run workflow with variables")
    parser_run.add_argument("workflow", nargs="?", help="Path to workflow.json (optional if using --template)")
    parser_run.add_argument("--template", "-t", help="Path to template file (from web UI 'Save Template')")
    parser_run.add_argument("--set", action="append", help="Set variable value: name=value")
    parser_run.add_argument("--file", action="append", help="Set file variable: name=path")
    parser_run.add_argument("--batch", "-b", help="Path to JSON file with batch variables")
    parser_run.add_argument("--out", "-o", default="./outputs", help="Output directory")
    parser_run.set_defaults(func=run)

    args = parser.parse_args()
    
    # Validate run command
    if args.command == "run" and not args.workflow and not args.template:
        parser.error("Either 'workflow' or '--template' is required for 'run' command")
    
    if asyncio.iscoroutinefunction(args.func):
        asyncio.run(args.func(args))
    else:
        args.func(args)


if __name__ == "__main__":
    main()
