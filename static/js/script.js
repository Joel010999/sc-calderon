/* ═══════════════════════════════════════════════════════
   SC VIAJES — Public site interactions (Local JS)
   Menú móvil, intercambio de ruta, scroll al buscador,
   FAQ y reveal-on-scroll. Sin dependencias externas.
   ═══════════════════════════════════════════════════════ */

'use strict';

document.addEventListener('DOMContentLoaded', () => {
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => [...document.querySelectorAll(sel)];

  const originSelect = $('#origin');
  const destSelect   = $('#destination');
  const menuToggle   = $('.menu-toggle');
  const siteNav      = $('.site-nav');

  // Swap route
  const swapBtn = $('#swap-route');
  if (swapBtn && originSelect && destSelect) {
    swapBtn.addEventListener('click', () => {
      const temp = originSelect.value;
      originSelect.value = destSelect.value;
      destSelect.value = temp;
    });
  }

  // Global click delegation
  document.addEventListener('click', (e) => {
    if (e.target.closest('[data-scroll-search="true"]')) {
      const card = $('.search-card');
      if (card) card.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  });

  // Mobile menu
  if (menuToggle && siteNav) {
    const setMenu = (open, moveFocus = false) => {
      menuToggle.setAttribute('aria-expanded', String(open));
      menuToggle.setAttribute('aria-label', open ? 'Cerrar menú principal' : 'Abrir menú principal');
      siteNav.classList.toggle('is-open', open);
      if (open && moveFocus) siteNav.querySelector('a, button')?.focus();
    };
    menuToggle.addEventListener('click', () => {
      const expanded = menuToggle.getAttribute('aria-expanded') === 'true';
      setMenu(!expanded, !expanded);
    });

    siteNav.addEventListener('click', (e) => {
      if (e.target.tagName === 'A' || e.target.classList.contains('nav-cta')) {
        setMenu(false);
      }
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && menuToggle.getAttribute('aria-expanded') === 'true') {
        setMenu(false);
        menuToggle.focus();
      }
    });
  }

  // FAQ accordion (solo uno abierto)
  const faqDetails = $$('.faq-list details');
  faqDetails.forEach(detail => {
    detail.addEventListener('toggle', () => {
      if (detail.open) {
        faqDetails.forEach(d => {
          if (d !== detail && d.open) d.open = false;
        });
      }
    });
  });

  // Scroll reveal (IntersectionObserver)
  const revealObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('is-visible');
        revealObserver.unobserve(entry.target);
      }
    });
  }, {
    threshold: 0.08,
    rootMargin: '0px 0px -30px 0px'
  });

  $$('.reveal').forEach(el => revealObserver.observe(el));
});
