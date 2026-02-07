# documents/admin.py
from django.contrib import admin
from .models import Document, DocumentChunk

class DocumentChunkInline(admin.StackedInline):
    model = DocumentChunk
    extra = 0
    fields = ('page_number', 'chunk_index', 'content')
    readonly_fields = ('page_number', 'chunk_index', 'content')

@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ('name', 'project', 'document_type', 'status', 'page_count', 'chunk_count')
    list_filter = ('document_type', 'status', 'project')
    search_fields = ('name',)
    readonly_fields = ('chunk_count', 'processed_at', 'created_at')
    inlines = [DocumentChunkInline]

@admin.register(DocumentChunk)
class DocumentChunkAdmin(admin.ModelAdmin):
    list_display = ('document', 'page_number', 'chunk_index')
    list_filter = ('document__project', 'document')
    readonly_fields = ('embedding',)