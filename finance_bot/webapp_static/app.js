const tg = window.Telegram?.WebApp;
tg?.ready();
tg?.expand();

const initData = tg?.initData || '';
const money = (value) => new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 }).format(Number(value || 0));
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { 'Content-Type': 'application/json', Authorization: `tma ${initData}`, ...(options.headers || {}) },
  });
  if (!response.ok) throw new Error((await response.json()).error || 'request_failed');
  return response.json();
}

function row(left, right, cls = '') { return `<div class="row"><span>${left}</span><strong class="amount ${cls}">${right}</strong></div>`; }

async function loadSummary() {
  const data = await api('/api/summary?period=month');
  document.getElementById('income').textContent = money(data.summary.income);
  document.getElementById('expense').textContent = money(data.summary.expense);
  document.getElementById('balance').textContent = money(data.summary.balance);
  document.getElementById('categories').innerHTML = Object.entries(data.expensesByCategory).map(([name, value]) => row(escapeHtml(name), money(value), 'danger')).join('') || '<p class="muted">Расходов пока нет</p>';
  document.getElementById('transactions').innerHTML = data.transactions.map((item) => row(`${escapeHtml(item.category)}<br><small class="muted">${new Date(item.created_at).toLocaleString('ru-RU')}</small>`, `${item.type === 'income' ? '+' : '-'}${money(item.amount)}`, item.type === 'income' ? 'success' : 'danger')).join('') || '<p class="muted">Операций пока нет</p>';
  document.getElementById('subscriptions').innerHTML = data.subscriptions.map((item) => `<div class="row"><span>${escapeHtml(item.name)}<br><small class="muted">${escapeHtml(item.next_payment_date)}</small></span><span><strong class="amount">${money(item.amount)}</strong><br><button data-paid="${item.id}" type="button">Оплачено</button></span></div>`).join('') || '<p class="muted">Подписок пока нет</p>';
  document.getElementById('status').textContent = 'Данные за текущий месяц';
}

async function submitForm(formId, path, mapPayload) {
  const form = document.getElementById(formId);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const payload = mapPayload(Object.fromEntries(new FormData(form)));
    await api(path, { method: 'POST', body: JSON.stringify(payload) });
    form.reset();
    await loadSummary();
    tg?.HapticFeedback?.notificationOccurred('success');
  });
}

submitForm('transaction-form', '/api/transactions', (data) => data);
submitForm('budget-form', '/api/budgets', (data) => data);
submitForm('subscription-form', '/api/subscriptions', (data) => data);

document.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-paid]');
  if (!button) return;
  await api(`/api/subscriptions/${button.dataset.paid}/paid`, { method: 'POST', body: '{}' });
  await loadSummary();
});

loadSummary().catch((error) => {
  document.getElementById('status').textContent = `Ошибка: ${error.message}`;
});
