from django.contrib import messages
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST
from .permissions import staff_management_access
from .staff_services import ROLES, accept_invitation, invite_staff, update_staff
from core.abuse import consume, request_identifier

@staff_management_access()
def staff_list(request):
    User=get_user_model(); q=request.GET.get("q","").strip(); role=request.GET.get("role","")
    qs=User.objects.filter(is_superuser=False,groups__name__in=ROLES).distinct().prefetch_related("groups").order_by("email")
    if q: qs=qs.filter(email__icontains=q)
    if role in ROLES: qs=qs.filter(groups__name=role)
    return render(request,"panel/staff/list.html",{"users":Paginator(qs,25).get_page(request.GET.get("page")),"q":q,"role":role,"roles":ROLES})

@staff_management_access()
def staff_invite(request):
    if request.method=="POST":
        if not consume(request, scope="panel.staff_invitation", limit=settings.ABUSE_STAFF_INVITATION_LIMIT,
                       window_seconds=settings.ABUSE_STAFF_INVITATION_WINDOW_SECONDS,
                       identifier=request_identifier(request, request.POST.get("email", "").strip().lower())):
            messages.error(request, "No pudimos procesar la invitación en este momento. Intentá nuevamente más tarde.")
            return render(request,"panel/staff/invite.html",{"roles":ROLES})
        try: invite_staff(actor=request.user,email=request.POST.get("email",""),role=request.POST.get("role","")); messages.success(request,"Invitación enviada."); return redirect("panel:staff_list")
        except ValidationError as exc: messages.error(request,str(exc))
    return render(request,"panel/staff/invite.html",{"roles":ROLES})

@require_POST
@staff_management_access()
def staff_update(request,user_id):
    try: update_staff(actor=request.user,user_id=user_id,role=request.POST.get("role") or None,active=request.POST.get("active")=="1"); messages.success(request,"Usuario actualizado.")
    except ValidationError as exc: messages.error(request,str(exc))
    return redirect("panel:staff_list")

def staff_accept(request,token):
    if request.method=="POST":
        if not consume(request, scope="panel.staff_invitation_accept", limit=settings.ABUSE_STAFF_INVITATION_LIMIT,
                       window_seconds=settings.ABUSE_STAFF_INVITATION_WINDOW_SECONDS,
                       identifier=request_identifier(request, token)):
            messages.error(request, "El enlace es inválido o venció.")
            return render(request,"panel/staff/accept.html",{"token":token})
        try: accept_invitation(token,request.POST.get("password","")); messages.success(request,"Contraseña establecida."); return redirect("panel:login")
        except ValidationError as exc: messages.error(request,str(exc))
    return render(request,"panel/staff/accept.html",{"token":token})
