import re
import copy

# Import the converter from client
from .client import convert_workflow_to_api

class WorkflowManager:
    """
    Manages ComfyUI workflow variable extraction and injection.
    Supports both legacy **name[type]** syntax and direct node traversal.
    Auto-detects and converts UI format workflows to API format.
    """

    VAR_PATTERN = re.compile(r"\*\*(?P<name>[\w_]+)\[(?P<type>\w+)\](?:\((?P<options>[^\)]+)\))?\*\*")

    @classmethod
    def is_ui_format(cls, workflow):
        """Check if workflow is in UI format (has 'nodes' and 'links' arrays)"""
        return isinstance(workflow.get("nodes"), list) and "links" in workflow

    @classmethod
    def ensure_api_format(cls, workflow):
        """
        Ensure workflow is in API format.
        If it's in UI format (saved from ComfyUI 'Save'), convert it.
        Also validates that all nodes have class_type.
        """
        if cls.is_ui_format(workflow):
            # Convert UI format to API format
            return convert_workflow_to_api(workflow)
        
        # Validate API format - check for missing class_type
        errors = []
        for node_id, node_data in workflow.items():
            if isinstance(node_data, dict) and "inputs" in node_data:
                if "class_type" not in node_data:
                    title = node_data.get("_meta", {}).get("title", "Unknown")
                    errors.append(f"Node #{node_id} ({title}) is missing 'class_type'")
        
        if errors:
            raise ValueError("Invalid workflow format:\n" + "\n".join(errors))
        
        return workflow

    @classmethod
    def extract_variables(cls, workflow_json):
        """
        Traverse the workflow dict and find all variables. (Legacy support)
        """
        # Ensure API format first
        workflow_json = cls.ensure_api_format(workflow_json)
        
        variables = {}
        
        def traverse(obj):
            if isinstance(obj, str):
                matches = cls.VAR_PATTERN.finditer(obj)
                for match in matches:
                    name = match.group("name")
                    var_type = match.group("type")
                    options = match.group("options")
                    
                    if name not in variables:
                         variables[name] = {
                            "name": name,
                            "type": var_type,
                            "options": options.split("|") if options else [],
                            "raw": match.group(0),
                            "mode": "regex"
                        }
            elif isinstance(obj, dict):
                for key, value in obj.items():
                    traverse(value)
            elif isinstance(obj, list):
                for item in obj:
                    traverse(item)

        traverse(workflow_json)
        return list(variables.values())

    @classmethod
    def scan_possible_inputs(cls, workflow_json):
        """
        Scans the workflow and returns a list of all detected scalar inputs.
        Auto-converts UI format to API format if needed.
        """
        # Ensure API format first
        workflow_json = cls.ensure_api_format(workflow_json)
        
        inputs = []
        
        # Iterate top-level nodes
        for node_id, node_data in workflow_json.items():
             if not isinstance(node_data, dict): continue
             
             node_title = node_data.get("_meta", {}).get("title", node_data.get("class_type", "Unknown"))
             node_inputs = node_data.get("inputs", {})
             
             for field, value in node_inputs.items():
                 # Skip links (lists usually [id, slot])
                 if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                     continue # It's a link
                 
                 # Guess type
                 var_type = "text"
                 if isinstance(value, (int, float)):
                     var_type = "number"
                 elif isinstance(value, str):
                     if value.startswith("**") and value.endswith("**"):
                         continue # Skip existing regex vars? No, show them too or strict skip?
                         # Let's strict skip to avoid confusion or treat as raw
                 
                 inputs.append({
                     "id": f"{node_id}.{field}", # Unique ID for UI (NodeID.Field)
                     "node_id": node_id,
                     "node_title": node_title,
                     "field": field,
                     "value": value,
                     "type": var_type
                 })
                 
        return inputs


    @classmethod
    def inject_variables(cls, workflow_json, values):
        """
        Replace variables in the workflow.
        Values keys can be:
        1. "variable_name" (mapped to Regex **var**)
        2. "node_id.field" (direct update)
        """
        workflow = copy.deepcopy(workflow_json)
        
        # 1. Direct Updates (NodeID.Field)
        for key, val in values.items():
            if "." in key:
                parts = key.split(".", 1)
                node_id = parts[0]
                field = parts[1]
                
                if node_id in workflow and "inputs" in workflow[node_id]:
                    # Update directly
                    # If existing value is a list (link) we probably shouldn't break it unless user forces
                    workflow[node_id]["inputs"][field] = cls._cast_value(val)
        
        # 2. Regex Replacements
        def traverse(obj):
            if isinstance(obj, str):
                # Check for regex variables
                match = cls.VAR_PATTERN.fullmatch(obj)
                if match:
                    name = match.group("name")
                    if name in values:
                        return cls._cast_value(values[name])
                
                # Partial
                new_str = obj
                matches = list(cls.VAR_PATTERN.finditer(obj))
                for match in reversed(matches):
                    name = match.group("name")
                    if name in values:
                         new_str = new_str[:match.start()] + str(values[name]) + new_str[match.end():]
                return new_str

            elif isinstance(obj, dict):
                return {k: traverse(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [traverse(item) for item in obj]
            else:
                return obj

        return traverse(workflow)

    @staticmethod
    def _cast_value(val):
        """Try to cast string numbers to int/float if they look like it"""
        if isinstance(val, str):
            try:
                # Naive check, only if it looks purely numerical
                 if val.replace(".", "", 1).isdigit():
                     if "." in val:
                         return float(val)
                     return int(val)
            except:
                pass
        return val
