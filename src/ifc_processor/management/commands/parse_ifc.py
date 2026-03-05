"""Management command to parse IFC files."""

from django.core.management.base import BaseCommand, CommandError

from ifc_processor.models import IFCFile
from ifc_processor.services.parser import IFCParser


class Command(BaseCommand):
    help = "Parse an IFC file and extract entities"

    def add_arguments(self, parser):
        parser.add_argument(
            "ifc_file_id",
            nargs="?",
            help="UUID of the IFC file to parse (or 'all' for all pending)",
        )
        parser.add_argument(
            "--all-pending",
            action="store_true",
            help="Parse all files with pending status",
        )
        parser.add_argument(
            "--reprocess",
            action="store_true",
            help="Reprocess files even if already completed",
        )

    def handle(self, *args, **options):
        if options["all_pending"]:
            files = IFCFile.objects.filter(status=IFCFile.Status.PENDING)
            self.stdout.write(f"Found {files.count()} pending files")

            for ifc_file in files:
                self._parse_file(ifc_file)

        elif options["ifc_file_id"]:
            try:
                ifc_file = IFCFile.objects.get(id=options["ifc_file_id"])
            except IFCFile.DoesNotExist:
                raise CommandError(f"IFC file not found: {options['ifc_file_id']}")

            if ifc_file.status == IFCFile.Status.COMPLETED and not options["reprocess"]:
                self.stdout.write(
                    self.style.WARNING("File already processed. Use --reprocess to re-parse.")
                )
                return

            self._parse_file(ifc_file)

        else:
            self.stdout.write(
                self.style.WARNING("No IFC file specified. Use --all-pending or provide an ID.")
            )

            # Show available files
            files = IFCFile.objects.all()[:10]
            if files:
                self.stdout.write("\nAvailable IFC files:")
                for f in files:
                    self.stdout.write(f"  {f.id} - {f.name} ({f.status})")

    def _parse_file(self, ifc_file):
        self.stdout.write(f"Parsing: {ifc_file.name}...")

        parser = IFCParser(ifc_file)
        success = parser.parse()

        if success:
            self.stdout.write(
                self.style.SUCCESS(f"✓ Parsed {ifc_file.name}: {parser.entities_created} entities")
            )
        else:
            self.stdout.write(
                self.style.ERROR(f"✗ Failed to parse {ifc_file.name}: {ifc_file.error_message}")
            )
