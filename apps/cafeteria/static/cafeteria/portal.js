(() => {
  const csrfToken = () => {
    const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    if (input) return input.value;
    const item = document.cookie.split('; ').find((entry) => entry.startsWith('csrftoken='));
    return item ? decodeURIComponent(item.split('=').slice(1).join('=')) : '';
  };

  const sidebar = document.querySelector('[data-sidebar]');
  if (sidebar) {
    const key = 'afa-ordis:sidebar-scroll';
    sidebar.scrollTop = Number(sessionStorage.getItem(key) || 0);
    sidebar.addEventListener('scroll', () => sessionStorage.setItem(key, String(sidebar.scrollTop)), { passive: true });
    document.querySelectorAll('[data-nav-section]').forEach((section) => {
      section.addEventListener('toggle', () => {
        fetch(sidebar.dataset.navigationUrl, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRFToken': csrfToken() },
          body: new URLSearchParams({ section: section.dataset.navSection, collapsed: section.open ? '0' : '1' }),
        }).catch(() => {});
      });
    });
  }

  const infoDialog = document.querySelector('#calendar-info-dialog');
  document.querySelectorAll('[data-calendar-info]').forEach((button) => {
    button.addEventListener('click', () => {
      if (!infoDialog) return;
      infoDialog.querySelector('[data-calendar-dialog-title]').textContent = button.dataset.calendarDate || '';
      infoDialog.querySelector('[data-calendar-dialog-summary]').textContent = button.dataset.calendarInfo || '';
      infoDialog.showModal();
    });
  });
  document.querySelectorAll('[data-close-dialog]').forEach((button) => button.addEventListener('click', () => button.closest('dialog')?.close()));

  const customizer = document.querySelector('[data-dashboard-customizer]');
  document.querySelectorAll('[data-open-dashboard-customizer]').forEach((button) => button.addEventListener('click', () => customizer?.showModal()));
  document.querySelectorAll('[data-widget-move]').forEach((button) => {
    button.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      const choice = button.closest('.widget-choice');
      const list = choice?.parentElement;
      if (!choice || !list) return;
      if (button.dataset.widgetMove === '-1' && choice.previousElementSibling) {
        list.insertBefore(choice, choice.previousElementSibling);
      }
      if (button.dataset.widgetMove === '1' && choice.nextElementSibling) {
        list.insertBefore(choice.nextElementSibling, choice);
      }
    });
  });
})();
