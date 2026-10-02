from django.shortcuts import render
from django.views.decorators.http import require_GET
from .inbox_services import build_inbox
from .permissions import operational_inbox_access


@operational_inbox_access()
@require_GET
def operational_inbox(request):
    return render(request, "panel/operational_inbox.html", build_inbox(params=request.GET))
