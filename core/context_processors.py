from .site_config import site_branding


def public_site(request):
    return {"site_brand": site_branding()}
