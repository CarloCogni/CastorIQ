# # writeback/admin.py
# from django.contrib import admin
# from django.utils.html import format_html
# from django.http import HttpResponseRedirect
# from django.shortcuts import render
# from django.utils import timezone
# from .models import ModificationProposal, GitCommit, Conflict
#
#
# @admin.register(ModificationProposal)
# class ModificationProposalAdmin(admin.ModelAdmin):
#     list_display = ('id_short', 'request_excerpt', 'ifc_file', 'colored_status', 'created_by', 'affected_count', 'created_at')
#     list_filter = ('status', 'created_at', 'ifc_file__project')
#     search_fields = ('request_text', 'explanation', 'ifc_file__name')
#     readonly_fields = ('id', 'created_at', 'updated_at', 'diff_preview_formatted')
#     date_hierarchy = 'created_at'
#     list_select_related = ('ifc_file', 'created_by', 'reviewed_by')
#     autocomplete_fields = ['message', 'ifc_file', 'created_by', 'reviewed_by']
#
#     fieldsets = (
#         ('Request & AI Analysis', {
#             'fields': ('id', 'message', 'ifc_file', 'created_by', 'request_text', 'explanation')
#         }),
#         ('Proposed Changes', {
#             'fields': ('changes', 'diff_preview_formatted', 'affected_count'),
#         }),
#         ('Review Status', {
#             'fields': ('status', 'reviewed_by', 'reviewed_at', 'rejection_reason'),
#         }),
#         ('Execution', {
#             'fields': ('git_commit',),
#             'classes': ('collapse',),
#         }),
#         ('Timestamps', {
#             'fields': ('created_at', 'updated_at'),
#             'classes': ('collapse',),
#         }),
#     )
#
#     actions = ['approve_proposals', 'reject_with_reason']
#
#     def id_short(self, obj):
#         return str(obj.id)[:8]
#     id_short.short_description = "ID"
#
#     def request_excerpt(self, obj):
#         return obj.request_text[:40] + "..." if len(obj.request_text) > 40 else obj.request_text
#     request_excerpt.short_description = "Request"
#
#     def colored_status(self, obj):
#         colors = {
#             'pending': '#f59e0b',    # warning/orange
#             'approved': '#10b981',   # success/green
#             'applied': '#3b82f6',    # primary/blue
#             'rejected': '#ef4444',   # danger/red
#             'failed': '#991b1b',     # dark red
#         }
#         return format_html(
#             '<span style="color:{}; font-weight:600;">{}</span>',
#             colors.get(obj.status, 'inherit'),
#             obj.get_status_display()
#         )
#     colored_status.short_description = "Status"
#     colored_status.admin_order_field = 'status'
#
#     def diff_preview_formatted(self, obj):
#         if obj.diff_preview:
#             return format_html('<pre style="margin:0; white-space:pre-wrap; font-size:12px;">{}</pre>', obj.diff_preview)
#         return "-"
#     diff_preview_formatted.short_description = "Diff Preview"
#
#     @admin.action(description="✅ Approve selected proposals")
#     def approve_proposals(self, request, queryset):
#         updated = queryset.filter(status='pending').update(
#             status='approved',
#             reviewed_by=request.user,
#             reviewed_at=timezone.now()
#         )
#         self.message_user(request, f"Approved {updated} proposal(s).")
#
#     @admin.action(description="❌ Reject with reason")
#     def reject_with_reason(self, request, queryset):
#         if 'apply' in request.POST:
#             reason = request.POST.get('reason', '')
#             updated = queryset.update(
#                 status='rejected',
#                 rejection_reason=reason,
#                 reviewed_by=request.user,
#                 reviewed_at=timezone.now()
#             )
#             self.message_user(request, f"Rejected {updated} proposal(s).")
#             return HttpResponseRedirect(request.get_full_path())
#
#         return render(request, 'admin/writeback/reject_reason_confirmation.html', context={
#             'title': 'Reject Proposals',
#             'proposals': queryset,
#             'opts': self.model._meta,
#             'action_checkbox_name': admin.helpers.ACTION_CHECKBOX_NAME,
#         })
#
#
# @admin.register(GitCommit)
# class GitCommitAdmin(admin.ModelAdmin):
#     list_display = ('commit_hash_short', 'message_excerpt', 'author', 'ifc_file', 'change_summary', 'created_at', 'view_on_github_button')
#     list_filter = ('ifc_file__project', 'author', 'created_at')
#     search_fields = ('commit_hash', 'message', 'ifc_file__name')
#     readonly_fields = ('id', 'commit_hash', 'parent_hash', 'diff_data_formatted', 'created_at', 'updated_at')
#     date_hierarchy = 'created_at'
#     list_select_related = ('ifc_file', 'author')
#
#     fieldsets = (
#         ('Commit Info', {
#             'fields': ('id', 'ifc_file', 'commit_hash', 'parent_hash', 'message', 'author')
#         }),
#         ('Change Summary', {
#             'fields': ('entities_modified', 'entities_added', 'entities_removed'),
#         }),
#         ('Diff Data', {
#             'fields': ('diff_data_formatted',),
#             'classes': ('collapse',),
#         }),
#     )
#
#     def commit_hash_short(self, obj):
#         return format_html('<code>{}</code>', obj.commit_hash[:8])
#     commit_hash_short.short_description = "Hash"
#     commit_hash_short.admin_order_field = 'commit_hash'
#
#     def message_excerpt(self, obj):
#         return obj.message[:50] + "..." if len(obj.message) > 50 else obj.message
#     message_excerpt.short_description = "Message"
#
#     def change_summary(self, obj):
#         parts = []
#         if obj.entities_modified:
#             parts.append(format_html('<span style="color:#f59e0b;">~{}</span>', obj.entities_modified))
#         if obj.entities_added:
#             parts.append(format_html('<span style="color:#10b981;">+{}</span>', obj.entities_added))
#         if obj.entities_removed:
#             parts.append(format_html('<span style="color:#ef4444;">-{}</span>', obj.entities_removed))
#         return format_html(' '.join(parts)) if parts else '-'
#     change_summary.short_description = "Changes"
#
#     def diff_data_formatted(self, obj):
#         import json
#         if obj.diff_data:
#             return format_html('<pre style="margin:0;">{}</pre>', json.dumps(obj.diff_data, indent=2))
#         return "-"
#     diff_data_formatted.short_description = "Diff Data"
#
#     def view_on_github_button(self, obj):
#         # TODO: Get from project settings or environment variable
#         url = f"https://github.com/CarloCogni/castor/commit/{obj.commit_hash}"
#         return format_html(
#             '<a class="button" href="{}" target="_blank" '
#             'style="background:#24292e; color:white; padding:3px 10px; border-radius:4px; text-decoration:none; font-size:11px;">'
#             '<span style="margin-right:4px;">⎋</span>GitHub</a>',
#             url
#         )
#     view_on_github_button.short_description = "Remote"
#
#
# @admin.register(Conflict)
# class ConflictAdmin(admin.ModelAdmin):
#     list_display = ('title', 'severity_badge', 'status_badge', 'project', 'created_at')
#     list_filter = ('severity', 'status', 'project')
#     search_fields = ('title', 'description', 'ifc_value', 'document_value')
#     readonly_fields = ('id', 'created_at', 'updated_at')
#     date_hierarchy = 'created_at'
#     list_select_related = ('project', 'ifc_entity', 'document_chunk', 'resolved_by')
#     autocomplete_fields = ['project', 'resolved_by']
#
#     fieldsets = (
#         ('Conflict Info', {
#             'fields': ('id', 'project', 'title', 'description', 'severity', 'status')
#         }),
#         ('Conflicting Values', {
#             'fields': ('ifc_entity', 'ifc_value', 'document_chunk', 'document_value'),
#         }),
#         ('Resolution', {
#             'fields': ('resolved_by', 'resolved_at', 'resolution_note'),
#         }),
#     )
#
#     actions = ['mark_resolved', 'mark_ignored']
#
#     def severity_badge(self, obj):
#         colors = {
#             'critical': '#ef4444',
#             'high': '#f59e0b',
#             'medium': '#3b82f6',
#             'low': '#6b7280',
#         }
#         return format_html(
#             '<span style="background:{}; color:white; padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600;">{}</span>',
#             colors.get(obj.severity, '#6b7280'),
#             obj.get_severity_display().upper()
#         )
#     severity_badge.short_description = "Severity"
#     severity_badge.admin_order_field = 'severity'
#
#     def status_badge(self, obj):
#         colors = {
#             'open': '#f59e0b',
#             'resolved': '#10b981',
#             'ignored': '#6b7280',
#         }
#         return format_html(
#             '<span style="color:{}; font-weight:600;">{}</span>',
#             colors.get(obj.status, 'inherit'),
#             obj.get_status_display()
#         )
#     status_badge.short_description = "Status"
#
#     @admin.action(description="✅ Mark as Resolved")
#     def mark_resolved(self, request, queryset):
#         updated = queryset.update(
#             status='resolved',
#             resolved_by=request.user,
#             resolved_at=timezone.now()
#         )
#         self.message_user(request, f"Marked {updated} conflict(s) as resolved.")
#
#     @admin.action(description="🙈 Mark as Ignored")
#     def mark_ignored(self, request, queryset):
#         updated = queryset.update(status='ignored')
#         self.message_user(request, f"Marked {updated} conflict(s) as ignored.")
