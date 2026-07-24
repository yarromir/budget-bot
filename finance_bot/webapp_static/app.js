const tg = window.Telegram?.WebApp;
tg?.ready();
tg?.expand();

const initData = tg?.initData || '';
let currentPeriod = 'month';
let toastTimer;

const money = (value) => `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 }).format(Number(value || 0))} ₽`;
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { 'Content-Type': 'application/json', Authorization: `tma ${initData}`, ...(options.headers || {}) },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || 'request_failed');
  return data;
}

function showToast(message) {
  const toast = document.getElementById('toast');
  toast.textContent = message;
  toast.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('show'), 2200);
}

function row(left, right, cls = '') {
  return `<div class="row"><span>${left}</span><strong class="amount ${cls}">${right}</strong></div>`;
}

function budgetRow(item) {
  const limit = Number(item.limit_amount || 0);
  const spent = Number(item.spent || 0);
  const percent = limit > 0 ? Math.round((spent / limit) * 100) : 0;
  const level = percent >= 100 ? 'exceeded' : percent >= 80 ? 'near' : '';
  return `<div class="row">
    <span>${escapeHtml(item.category)}<br><small class="muted">${money(spent)} из ${money(limit)} · ${percent}%</small><div class="progress ${level}" style="--value:${Math.min(percent, 100)}%"><span></span></div></span>
    <strong class="amount ${level === 'exceeded' ? 'danger' : ''}">${money(limit - spent)}</strong>
  </div>`;
}

function reminderSummary(item) {
  const days = [
    [item.remind_7, 'за 7 дней'],
    [item.remind_3, 'за 3 дня'],
    [item.remind_1, 'за 1 день'],
  ].filter(([enabled]) => Number(enabled) === 1).map(([, label]) => `<span>${label}</span>`);
  return days.length ? `<span class="reminder-summary">${days.join('')}</span>` : '<span class="reminder-summary"><span>без напоминаний</span></span>';
}

async function loadSummary() {
  const data = await api(`/api/summary?period=${currentPeriod}`);
  document.getElementById('income').textContent = money(data.summary.income);
  document.getElementById('expense').textContent = money(data.summary.expense);
  document.getElementById('balance').textContent = money(data.summary.balance);
  document.getElementById('categories').innerHTML = Object.entries(data.expensesByCategory).map(([name, value]) => row(escapeHtml(name), money(value), 'danger')).join('') || '<p class="muted">Расходов пока нет</p>';
  document.getElementById('budgets').innerHTML = (data.budgets || []).map(budgetRow).join('') || '<p class="muted">Лимитов пока нет — добавьте первый выше.</p>';
  document.getElementById('transactions').innerHTML = data.transactions.map((item) => row(`${escapeHtml(item.category)}<br><small class="muted">${new Date(item.created_at).toLocaleString('ru-RU')}${item.note ? ` · ${escapeHtml(item.note)}` : ''}</small>`, `${item.type === 'income' ? '+' : '-'}${money(item.amount)}`, item.type === 'income' ? 'success' : 'danger')).join('') || '<p class="muted">Операций пока нет</p>';
  document.getElementById('subscriptions').innerHTML = data.subscriptions.map((item) => `<div class="row"><span>${escapeHtml(item.name)}<br><small class="muted">Следующий платеж: ${escapeHtml(item.next_payment_date)}</small><br>${reminderSummary(item)}</span><span><strong class="amount">${money(item.amount)}</strong><br><button data-paid="${item.id}" type="button">Оплачено</button></span></div>`).join('') || '<p class="muted">Подписок пока нет</p>';
  document.getElementById('status').textContent = currentPeriod === 'month' ? 'Данные за текущий месяц' : 'Данные за сегодня';
}

function submitForm(formId, path, mapPayload, successMessage) {
  const form = document.getElementById(formId);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const submitButton = form.querySelector('[type="submit"]');
    submitButton.disabled = true;
    try {
      const payload = mapPayload(Object.fromEntries(new FormData(form)));
      await api(path, { method: 'POST', body: JSON.stringify(payload) });
      form.reset();
      if (formId === 'transaction-form') form.querySelector('[value="expense"]').checked = true;
      if (formId === 'subscription-form') form.querySelectorAll('[name^="remind"]').forEach((input) => { input.checked = true; });
      await loadSummary();
      showToast(successMessage);
      tg?.HapticFeedback?.notificationOccurred('success');
    } catch (error) {
      showToast(`Ошибка: ${error.message}`);
      tg?.HapticFeedback?.notificationOccurred('error');
    } finally {
      submitButton.disabled = false;
    }
  });
}

submitForm('transaction-form', '/api/transactions', (data) => data, 'Операция добавлена');
submitForm('budget-form', '/api/budgets', (data) => data, 'Лимит сохранен');
submitForm('subscription-form', '/api/subscriptions', (data) => ({
  ...data,
  remind7: data.remind7 === 'on',
  remind3: data.remind3 === 'on',
  remind1: data.remind1 === 'on',
}), 'Подписка добавлена');

document.querySelectorAll('[data-period]').forEach((button) => {
  button.addEventListener('click', async () => {
    currentPeriod = button.dataset.period;
    document.querySelectorAll('[data-period]').forEach((item) => item.classList.toggle('active', item === button));
    await loadSummary().catch((error) => showToast(`Ошибка: ${error.message}`));
  });
});

document.querySelectorAll('[data-category]').forEach((button) => {
  button.addEventListener('click', () => {
    document.getElementById('transaction-category').value = button.dataset.category;
  });
});

document.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-paid]');
  if (!button) return;
  button.disabled = true;
  try {
    await api(`/api/subscriptions/${button.dataset.paid}/paid`, { method: 'POST', body: '{}' });
    await loadSummary();
    showToast('Подписка отмечена оплаченной');
  } catch (error) {
    showToast(`Ошибка: ${error.message}`);
  } finally {
    button.disabled = false;
  }
});

loadSummary().catch((error) => {
  document.getElementById('status').textContent = `Ошибка: ${error.message}`;
});
