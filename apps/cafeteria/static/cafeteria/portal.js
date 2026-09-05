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

  const bookingCalendar = document.querySelector('[data-booking-calendar]');
  if (bookingCalendar) {
    const status = bookingCalendar.querySelector('[data-booking-status]');
    const applyPanel = bookingCalendar.querySelector('[data-apply-panel]');
    const updateUrl = bookingCalendar.dataset.updateUrl;
    const applyUrl = bookingCalendar.dataset.applyUrl;

    const setStatus = (message, isError = false) => {
      if (!status) return;
      status.textContent = message || '';
      status.dataset.status = isError ? 'error' : 'success';
    };

    const closeDietMenus = () => bookingCalendar.querySelectorAll('[data-diet-menu]').forEach((menu) => { menu.hidden = true; });
    const cellFor = (studentId, serviceDate) => bookingCalendar.querySelector(
      `[data-booking-cell][data-student-id="${studentId}"][data-date="${serviceDate}"]`,
    );
    const applyBookingState = (cell, booking) => {
      if (!cell || !booking) return;
      cell.dataset.state = booking.state;
      cell.dataset.dietName = booking.diet_name || '';
      const mainButton = cell.querySelector('[data-booking-main]');
      if (mainButton) {
        mainButton.disabled = cell.dataset.locked === 'true' || booking.state === 'unavailable';
        const action = booking.reserved ? bookingCalendar.dataset.cancelLabel : bookingCalendar.dataset.reserveLabel;
        mainButton.setAttribute('aria-label', `${action} ${cell.dataset.studentName} · ${cell.dataset.date}`);
      }
      const dietButton = cell.querySelector('[data-diet-trigger]');
      if (dietButton) dietButton.title = booking.diet_name || '';
      cell.querySelectorAll('[data-diet-choice]').forEach((choice) => {
        if (String(booking.diet_id || '') === choice.dataset.dietId) {
          choice.setAttribute('aria-current', 'true');
        } else {
          choice.removeAttribute('aria-current');
        }
      });
      closeDietMenus();
    };
    const request = async (url, values) => {
      const body = new URLSearchParams();
      Object.entries(values).forEach(([key, value]) => {
        if (Array.isArray(value)) value.forEach((item) => body.append(key, item));
        else body.append(key, value);
      });
      const response = await fetch(url, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
          'X-CSRFToken': csrfToken(),
          'Accept': 'application/json',
        },
        body,
      });
      let data;
      try {
        data = await response.json();
      } catch (_error) {
        data = { ok: false, message: bookingCalendar.dataset.failedLabel };
      }
      if (!response.ok || !data.ok) throw new Error(data.message || bookingCalendar.dataset.failedLabel);
      return data;
    };
    const setBusy = (cell, busy) => {
      cell.dataset.saving = busy ? 'true' : 'false';
      cell.querySelectorAll('button').forEach((button) => {
        if (!button.closest('[data-diet-menu]')) button.disabled = busy || cell.dataset.locked === 'true' || cell.dataset.state === 'unavailable';
      });
    };
    const showApplyPanel = (cell) => {
      if (!applyPanel) return;
      const sourceId = cell.dataset.studentId;
      const serviceDate = cell.dataset.date;
      applyPanel.dataset.sourceStudentId = sourceId;
      applyPanel.dataset.serviceDate = serviceDate;
      applyPanel.querySelectorAll('[data-apply-student]').forEach((choice) => {
        const targetCell = cellFor(choice.value, serviceDate);
        const eligible = choice.value !== sourceId && targetCell && targetCell.dataset.state === 'empty' && targetCell.dataset.locked !== 'true';
        choice.disabled = !eligible;
        choice.checked = Boolean(eligible);
      });
      const description = applyPanel.querySelector('[data-apply-description]');
      if (description) description.textContent = `${cell.dataset.date} · ${bookingCalendar.dataset.applyDefaultLabel}`;
      applyPanel.hidden = false;
      applyPanel.querySelector('[data-apply-booking]')?.focus();
    };

    bookingCalendar.addEventListener('click', async (event) => {
      const dietChoice = event.target.closest('[data-diet-choice]');
      const dietTrigger = event.target.closest('[data-diet-trigger]');
      const mainButton = event.target.closest('[data-booking-main]');
      if (dietChoice) {
        const cell = dietChoice.closest('[data-booking-cell]');
        if (!cell || cell.dataset.locked === 'true') return;
        setBusy(cell, true);
        try {
          const data = await request(updateUrl, {
            student_id: cell.dataset.studentId,
            service_date: cell.dataset.date,
            operation: 'diet',
            diet_id: dietChoice.dataset.dietId,
          });
          applyBookingState(cell, data.booking);
          setStatus(bookingCalendar.dataset.savedLabel);
        } catch (error) {
          setStatus(error.message, true);
        } finally {
          setBusy(cell, false);
        }
        return;
      }
      if (dietTrigger) {
        const cell = dietTrigger.closest('[data-booking-cell]');
        if (!cell || cell.dataset.state === 'empty' || cell.dataset.locked === 'true') return;
        const menu = cell.querySelector('[data-diet-menu]');
        const wasHidden = menu?.hidden;
        closeDietMenus();
        if (menu) menu.hidden = !wasHidden;
        return;
      }
      if (!mainButton) return;
      const cell = mainButton.closest('[data-booking-cell]');
      if (!cell || cell.dataset.locked === 'true' || cell.dataset.state === 'unavailable') return;
      const operation = cell.dataset.state === 'empty' ? 'reserve' : 'cancel';
      setBusy(cell, true);
      try {
        const data = await request(updateUrl, {
          student_id: cell.dataset.studentId,
          service_date: cell.dataset.date,
          operation,
        });
        applyBookingState(cell, data.booking);
        setStatus(bookingCalendar.dataset.savedLabel);
        if (operation === 'reserve' && data.booking.reserved) showApplyPanel(cell);
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        setBusy(cell, false);
      }
    });

    bookingCalendar.querySelector('[data-apply-booking]')?.addEventListener('click', async () => {
      const sourceStudentId = applyPanel?.dataset.sourceStudentId;
      const serviceDate = applyPanel?.dataset.serviceDate;
      const studentIds = [...applyPanel.querySelectorAll('[data-apply-student]:checked:not(:disabled)')].map((choice) => choice.value);
      if (!sourceStudentId || !serviceDate || !studentIds.length) {
        if (applyPanel) applyPanel.hidden = true;
        return;
      }
      const button = applyPanel.querySelector('[data-apply-booking]');
      button.disabled = true;
      try {
        const data = await request(applyUrl, {
          source_student_id: sourceStudentId,
          service_date: serviceDate,
          student_ids: studentIds,
        });
        data.bookings.forEach((entry) => applyBookingState(cellFor(entry.student_id, serviceDate), entry.booking));
        setStatus(data.updated ? bookingCalendar.dataset.savedLabel : bookingCalendar.dataset.noChangesLabel);
        applyPanel.hidden = true;
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        button.disabled = false;
      }
    });
    bookingCalendar.querySelector('[data-close-apply]')?.addEventListener('click', () => { if (applyPanel) applyPanel.hidden = true; });
  }

  document.querySelectorAll('[data-receipt-upload]').forEach((form) => {
    const previewList = form.querySelector('[data-receipt-previews]');
    const inputs = [...form.querySelectorAll('[data-camera-input], [data-file-input]')];
    if (!previewList || !inputs.length || !window.DataTransfer) return;
    const selected = [];

    const syncInput = (input) => {
      const transfer = new DataTransfer();
      selected.filter((item) => item.input === input).forEach((item) => transfer.items.add(item.file));
      input.files = transfer.files;
    };
    const render = () => {
      previewList.replaceChildren();
      selected.forEach((item) => {
        const row = document.createElement('li');
        row.className = 'receipt-preview-item';
        if (item.file.type.startsWith('image/')) {
          const image = document.createElement('img');
          image.alt = '';
          image.src = URL.createObjectURL(item.file);
          image.addEventListener('load', () => URL.revokeObjectURL(image.src), { once: true });
          row.append(image);
        } else {
          const icon = document.createElement('span');
          icon.textContent = 'PDF';
          row.append(icon);
        }
        const name = document.createElement('span');
        name.textContent = item.file.name;
        row.append(name);
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'icon-button';
        remove.textContent = '×';
        remove.setAttribute('aria-label', `Elimina ${item.file.name}`);
        remove.addEventListener('click', () => {
          const index = selected.indexOf(item);
          if (index >= 0) selected.splice(index, 1);
          syncInput(item.input);
          render();
        });
        row.append(remove);
        previewList.append(row);
      });
    };
    inputs.forEach((input) => input.addEventListener('change', () => {
      for (let index = selected.length - 1; index >= 0; index -= 1) {
        if (selected[index].input === input) selected.splice(index, 1);
      }
      [...input.files].forEach((file) => selected.push({ input, file }));
      render();
    }));
  });

  const installButtons = [...document.querySelectorAll('[data-install-app]')];
  const isStandalone = window.matchMedia?.('(display-mode: standalone)').matches || window.navigator.standalone === true;
  const isIOS = /iPad|iPhone|iPod/.test(window.navigator.userAgent) && !window.MSStream;
  let installPrompt;
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/service-worker.js', { scope: '/' }).catch(() => {});
  }
  if (!isStandalone && installButtons.length) {
    if (isIOS) installButtons.forEach((button) => { button.hidden = false; });
    window.addEventListener('beforeinstallprompt', (event) => {
      event.preventDefault();
      installPrompt = event;
      installButtons.forEach((button) => { button.hidden = false; });
    });
    installButtons.forEach((button) => button.addEventListener('click', async () => {
      if (installPrompt) {
        installPrompt.prompt();
        await installPrompt.userChoice;
        installPrompt = null;
        installButtons.forEach((item) => { item.hidden = true; });
      } else if (isIOS) {
        window.alert(button.dataset.iosMessage);
      }
    }));
  }
})();
