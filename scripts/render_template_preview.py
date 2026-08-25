"""Dev-only helper to preview calendar templates in VS Code / browser.

Usage:
    cd scripts
    python render_template_preview.py [--template-dir TEMPLATE_DIR] [--output OUTPUT_FILE]

This script loads the sample event context from preview_sample.json in the template
directory and renders the template.html with it, writing the result to a local
*.preview.html file. You can then open the file in VS Code's Simple Browser or a
web browser to visually inspect the template output.

This is a development tool and is not part of the sync workflow. No Xibo/Graph API calls
are made by this script.

Examples:
    # Preview the bundled calendar template with sample data
    python render_template_preview.py

    # Preview a custom template directory
    python render_template_preview.py --template-dir ../my-templates/calendar

    # Output to a specific file
    python render_template_preview.py --output /tmp/preview.html
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from xibo_sync import html_packaging


def load_preview_sample(template_dir: Path) -> dict[str, Any]:
    """Load sample context from preview_sample.json in the template directory."""
    preview_file = template_dir / "preview_sample.json"
    
    if not preview_file.exists():
        raise FileNotFoundError(
            f"Preview sample file not found: {preview_file}\n"
            "Expected a preview_sample.json in the template directory with sample event data."
        )
    
    with open(preview_file) as f:
        return json.load(f)


def preview_template(
    template_dir: Path,
    template_file: str = "template.html",
    output_file: Path | None = None,
) -> Path:
    """Render a template with sample data and write to a preview file.
    
    Args:
        template_dir: Directory containing template and sample files.
        template_file: Filename of the template to render (default: template.html).
        output_file: Path for output HTML file (default: template_dir/template.preview.html).
    
    Returns:
        Path to the generated preview HTML file.
    """
    # Load sample context
    context = load_preview_sample(template_dir)
    
    # Render template
    html_content = html_packaging.render_template(template_dir, template_file, context)
    
    # Determine output path
    if output_file is None:
        output_file = template_dir / f"{template_file.replace('.html', '.preview.html')}"
    else:
        output_file = Path(output_file)
    
    # Write preview
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(html_content)
    
    return output_file


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preview a calendar template with sample event data.",
        epilog="The generated *.preview.html file can be opened in VS Code or a web browser.",
    )
    parser.add_argument(
        "--template-dir",
        type=Path,
        default=Path(__file__).parent / "templates" / "calendar",
        help="Template directory containing template.html and views/*.json (default: scripts/templates/calendar)",
    )
    parser.add_argument(
        "--template",
        default="template.html",
        help="Template filename within the template directory (default: template.html)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output file for the generated preview HTML (default: {template-dir}/{template}.preview.html)",
    )
    
    args = parser.parse_args()
    
    try:
        template_dir = args.template_dir.resolve()
        if not template_dir.exists():
            print(f"[ERROR] Template directory not found: {template_dir}", file=sys.stderr)
            return 1
        
        print(f"[INFO] Template directory: {template_dir}")
        print(f"[INFO] Template file: {args.template}")
        
        output_file = preview_template(template_dir, args.template, args.output)
        
        print(f"[OK] Preview written to: {output_file}")
        print(f"\n[TIP] Open this file in VS Code (right-click > Open in Simple Browser)")
        print(f"   or in your web browser to preview the template output.")
        return 0
        
    except FileNotFoundError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[ERROR] Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
