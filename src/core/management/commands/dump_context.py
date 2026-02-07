import os
from pathlib import Path
from django.core.management.base import BaseCommand
from django.conf import settings


class Command(BaseCommand):
    help = 'Dumps the project source code into a single text file for LLM context.'

    # Configuration
    OUTPUT_FILE_NAME = 'project_context.txt'

    # Extensions to include
    INCLUDE_EXTS = {'.py', '.html', '.css', '.js', '.json', '.md'}

    # Directories to ignore completely
    IGNORE_DIRS = {
        '__pycache__',
        'migrations',
        '.git',
        '.venv',
        'venv',
        'env',
        'static',  # Compiled/collected static files
        'staticfiles',
        'media',  # User uploads
        'node_modules',
        '.idea',
        '.vscode',
        'tests'  # Optional: exclude tests to save tokens
    }

    # Specific files to ignore
    IGNORE_FILES = {
        'db.sqlite3',
        '.env',
        'poetry.lock',
        'uv.lock',
        'package-lock.json',
        'project_context.txt'  # Don't include the output file itself!
    }

    def handle(self, *args, **options):
        # settings.BASE_DIR points to 'src' based on your settings.py
        base_dir = settings.BASE_DIR

        # CHANGE: Output directly into src/
        output_path = base_dir / self.OUTPUT_FILE_NAME

        self.stdout.write(f"📂 Scanning project from: {base_dir}")
        self.stdout.write(f"🚫 Ignoring: {', '.join(self.IGNORE_DIRS)}")

        file_count = 0

        try:

            with open(output_path, 'w', encoding='utf-8') as outfile:
                # Include PROJECT_CONTEXT.md at the top if it exists
                context_file = base_dir / 'PROJECT_CONTEXT.md'
                if context_file.exists():
                    self.stdout.write(f"📂 PROJECT_CONTEXT.md found!")

                    outfile.write("\n" + "=" * 50 + "\n")
                    outfile.write("FILE: PROJECT_CONTEXT.md (PROJECT BRIEFING)\n")
                    outfile.write("=" * 50 + "\n\n")
                    with open(context_file, 'r', encoding='utf-8') as f:
                        outfile.write(f.read())
                    outfile.write("\n")
                    file_count += 1
                else:
                    self.stdout.write(f"Project context file not found!")

                outfile.write(f"PROJECT CONTEXT DUMP\n")
                outfile.write(f"Generated from: {base_dir}\n")
                outfile.write("=" * 50 + "\n\n")

                for root, dirs, files in os.walk(base_dir):
                    # 1. Filter directories in-place
                    dirs[:] = [d for d in dirs if d not in self.IGNORE_DIRS]

                    for file in files:
                        # 2. Filter specific files
                        if file in self.IGNORE_FILES:
                            continue

                        # 3. Filter by extension
                        _, ext = os.path.splitext(file)
                        if ext.lower() not in self.INCLUDE_EXTS:
                            continue

                        file_path = Path(root) / file

                        # Calculate relative path from src/ for cleaner reading
                        try:
                            rel_path = file_path.relative_to(base_dir)
                        except ValueError:
                            rel_path = file_path

                        # 4. Write to the dump file
                        outfile.write(f"\n{'=' * 50}\n")
                        outfile.write(f"FILE: {rel_path}\n")
                        outfile.write(f"{'=' * 50}\n\n")

                        try:
                            with open(file_path, 'r', encoding='utf-8', errors='ignore') as infile:
                                content = infile.read()
                                if not content.strip():
                                    outfile.write("(Empty File)\n")
                                else:
                                    outfile.write(content)
                                    outfile.write("\n")
                                file_count += 1
                        except Exception as e:
                            outfile.write(f"Error reading file: {e}\n")

            self.stdout.write(self.style.SUCCESS(f"✅ Successfully dumped {file_count} files."))
            self.stdout.write(self.style.SUCCESS(f"📄 Output saved to: {output_path}"))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Error creating dump: {e}"))


