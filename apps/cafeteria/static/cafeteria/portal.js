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

  const processedAllergyGroups = new Set();
  const setupAllergyFields = (scope = document) => {
    scope.querySelectorAll('[data-allergy-declaration]').forEach((input) => {
      const form = input.closest('form');
      const key = input.dataset.allergyDeclaration;
      const groupId = `${input.form?.id || form?.action || 'form'}:${input.name}`;
      if (!form || processedAllergyGroups.has(groupId)) return;
      processedAllergyGroups.add(groupId);
      const fields = [...form.querySelectorAll('[data-allergy-field]')]
        .filter((field) => field.dataset.allergyField === key)
        .map((field) => field.closest('.field'))
        .filter(Boolean);
      const syncAllergyFields = () => {
        const selected = [...form.querySelectorAll('[data-allergy-declaration]')]
          .find((choice) => choice.name === input.name && choice.checked);
        const visible = selected?.value === 'yes';
        fields.forEach((field) => { field.hidden = !visible; });
      };
      form.querySelectorAll('[data-allergy-declaration]').forEach((choice) => {
        if (choice.name !== input.name) return;
        choice.addEventListener('change', syncAllergyFields);
      });
      syncAllergyFields();
    });
  };
  setupAllergyFields();

  const studentFormset = document.querySelector('[data-student-formset]');
  if (studentFormset) {
    const totalForms = studentFormset.querySelector('[name="new-students-TOTAL_FORMS"]');
    const list = studentFormset.querySelector('[data-student-form-list]');
    const template = studentFormset.querySelector('[data-student-form-template]');
    const addButton = studentFormset.querySelector('[data-add-student-form]');
    addButton?.addEventListener('click', () => {
      if (!totalForms || !list || !template) return;
      const index = Number(totalForms.value);
      const fragment = document.createRange().createContextualFragment(
        template.innerHTML.replaceAll('__prefix__', String(index)),
      );
      list.append(fragment);
      totalForms.value = String(index + 1);
      setupAllergyFields(list.lastElementChild);
    });
    studentFormset.addEventListener('click', (event) => {
      const button = event.target.closest('[data-remove-student-form]');
      if (!button) return;
      const row = button.closest('[data-student-form-row]');
      const deleted = row?.querySelector('[data-student-delete]');
      if (!row || !deleted) return;
      deleted.value = 'on';
      row.hidden = true;
    });
  }

  const bookingCalendar = document.querySelector('[data-booking-calendar]');
  if (bookingCalendar) {
    const status = bookingCalendar.querySelector('[data-booking-status]');
    const updateUrl = bookingCalendar.dataset.updateUrl;
    const applyUrl = bookingCalendar.dataset.applyUrl;
    const isFamilyCalendar = bookingCalendar.dataset.bookingKind === 'family';
    const familyBatchSwitch = bookingCalendar.querySelector('[data-family-batch-switch]');
    const familyTabs = [...bookingCalendar.querySelectorAll('[data-family-calendar-tab]')];
    const familyPanels = [...bookingCalendar.querySelectorAll('[data-family-calendar-panel]')];

    if (familyTabs.length && familyPanels.length) {
      const storageKey = `afa-ordis:family-calendar:${bookingCalendar.dataset.tabsKey || 'default'}:student`;
      const selectStudentTab = (studentId, focus = false) => {
        const selected = String(studentId);
        familyTabs.forEach((tab) => {
          const active = tab.dataset.studentId === selected;
          tab.setAttribute('aria-selected', String(active));
          tab.tabIndex = active ? 0 : -1;
          if (active && focus) tab.focus();
        });
        familyPanels.forEach((panel) => { panel.hidden = panel.dataset.studentId !== selected; });
        try { sessionStorage.setItem(storageKey, selected); } catch (_error) { /* Private browsing can disable storage. */ }
      };
      let storedStudent;
      try { storedStudent = sessionStorage.getItem(storageKey); } catch (_error) { storedStudent = null; }
      if (storedStudent && familyTabs.some((tab) => tab.dataset.studentId === storedStudent)) selectStudentTab(storedStudent);
      familyTabs.forEach((tab, index) => {
        tab.addEventListener('click', () => selectStudentTab(tab.dataset.studentId));
        tab.addEventListener('keydown', (event) => {
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
          event.preventDefault();
          let nextIndex = index;
          if (event.key === 'ArrowLeft') nextIndex = (index - 1 + familyTabs.length) % familyTabs.length;
          if (event.key === 'ArrowRight') nextIndex = (index + 1) % familyTabs.length;
          if (event.key === 'Home') nextIndex = 0;
          if (event.key === 'End') nextIndex = familyTabs.length - 1;
          selectStudentTab(familyTabs[nextIndex].dataset.studentId, true);
        });
      });
    }

    const setStatus = (message, isError = false) => {
      if (!status) return;
      status.textContent = message || '';
      status.dataset.status = isError ? 'error' : 'success';
    };

    const closeDietMenus = () => bookingCalendar.querySelectorAll('[data-diet-menu]').forEach((menu) => { menu.hidden = true; });
    const cellsFor = (studentId, serviceDate) => [...bookingCalendar.querySelectorAll(
      `[data-booking-cell][data-student-id="${studentId}"][data-date="${serviceDate}"]`,
    )];
    const cellsForDate = (serviceDate) => [...bookingCalendar.querySelectorAll(
      `[data-booking-cell][data-date="${serviceDate}"]`,
    )];
    const applyBookingState = (cell, booking) => {
      if (!cell || !booking) return;
      cell.dataset.state = booking.state;
      cell.dataset.dietName = booking.diet_name || '';
      const mainButton = cell.querySelector('[data-booking-main]');
      if (mainButton) {
        mainButton.disabled = cell.dataset.locked === 'true' || booking.state === 'unavailable';
        const action = booking.reserved ? bookingCalendar.dataset.cancelLabel : bookingCalendar.dataset.reserveLabel;
        const person = cell.dataset.studentName ? ` ${cell.dataset.studentName} ·` : '';
        mainButton.setAttribute('aria-label', `${action}${person} ${cell.dataset.date}`);
      }
      const dietButton = cell.querySelector('[data-diet-trigger]');
      if (dietButton) {
        dietButton.title = booking.diet_name || '';
        dietButton.disabled = !booking.reserved || cell.dataset.locked === 'true';
      }
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

    bookingCalendar.addEventListener('click', async (event) => {
      const dietChoice = event.target.closest('[data-diet-choice]');
      const dietTrigger = event.target.closest('[data-diet-trigger]');
      const mainButton = event.target.closest('[data-booking-main]');
      if (dietChoice) {
        const cell = dietChoice.closest('[data-booking-cell]');
        if (!cell || cell.dataset.locked === 'true') return;
        setBusy(cell, true);
        try {
          const values = {
            service_date: cell.dataset.date,
            operation: 'diet',
            diet_id: dietChoice.dataset.dietId,
          };
          if (isFamilyCalendar) values.student_id = cell.dataset.studentId;
          const data = await request(updateUrl, values);
          applyBookingState(cell, data.booking);
          if (isFamilyCalendar) cellsFor(cell.dataset.studentId, cell.dataset.date).forEach((item) => applyBookingState(item, data.booking));
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
      const batch = isFamilyCalendar && familyBatchSwitch?.checked;
      const affectedCells = batch ? cellsForDate(cell.dataset.date) : [cell];
      affectedCells.forEach((item) => setBusy(item, true));
      try {
        if (batch) {
          const data = await request(applyUrl, { service_date: cell.dataset.date });
          data.bookings.forEach((entry) => {
            cellsFor(entry.student_id, cell.dataset.date).forEach((item) => applyBookingState(item, entry.booking));
          });
          setStatus(data.updated ? bookingCalendar.dataset.batchSavedLabel : bookingCalendar.dataset.noChangesLabel);
        } else {
          const values = { service_date: cell.dataset.date, operation };
          if (isFamilyCalendar) values.student_id = cell.dataset.studentId;
          const data = await request(updateUrl, values);
          applyBookingState(cell, data.booking);
          if (isFamilyCalendar) cellsFor(cell.dataset.studentId, cell.dataset.date).forEach((item) => applyBookingState(item, data.booking));
          setStatus(bookingCalendar.dataset.savedLabel);
        }
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        affectedCells.forEach((item) => setBusy(item, false));
      }
    });
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
        remove.setAttribute('aria-label', `${previewList.dataset.removeLabel} ${item.file.name}`);
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
