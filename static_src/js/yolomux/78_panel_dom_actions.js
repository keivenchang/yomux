function domBuilderDataAttributeName(key) {
  return `data-${String(key).replace(/[A-Z]/g, match => `-${match.toLowerCase()}`)}`;
}

function setDomBuilderOptions(element, options = {}) {
  if (!element) return element;
  if (options.id) element.id = options.id;
  if (options.className) element.className = options.className;
  if (options.role) element.setAttribute('role', options.role);
  if (options.title) element.title = options.title;
  if (options.ariaLabel) element.setAttribute('aria-label', options.ariaLabel);
  if (options.ariaHidden !== undefined) element.setAttribute('aria-hidden', options.ariaHidden ? 'true' : 'false');
  if (options.hidden !== undefined) element.hidden = options.hidden === true;
  if (options.dataset) {
    for (const [key, value] of Object.entries(options.dataset)) {
      if (value !== undefined && value !== null && value !== false) element.dataset[key] = value === true ? '' : String(value);
    }
  }
  if (options.attributes) {
    for (const [name, value] of Object.entries(options.attributes)) {
      if (!name || value === undefined || value === null || value === false) continue;
      element.setAttribute(name, value === true ? '' : String(value));
    }
  }
  if (options.html !== undefined) element.innerHTML = options.html;
  else if (options.label !== undefined) element.textContent = options.label;
  return element;
}

function domBuilderSerializedAttributes(element) {
  const attrs = [];
  const seen = new Set();
  const add = (name, value) => {
    if (!name || value === undefined || value === null || value === false || seen.has(name)) return;
    seen.add(name);
    if (value === true) attrs.push(` ${name}`);
    else attrs.push(` ${name}="${esc(value)}"`);
  };
  const attrMap = element?.attributes || {};
  if (element?.id) add('id', element.id);
  if (String(element?.localName || '').toLowerCase() === 'button') add('type', element.type || attrMap.type || 'button');
  const className = typeof element?.className === 'string' ? element.className : '';
  if (className) add('class', className);
  if (element?.title) add('title', element.title);
  if (typeof attrMap.length === 'number') {
    for (const attr of Array.from(attrMap)) add(attr.name, attr.value);
  } else {
    for (const [name, value] of Object.entries(attrMap)) add(name, value);
  }
  if (element?.dataset && typeof element.dataset === 'object') {
    for (const [key, value] of Object.entries(element.dataset)) add(domBuilderDataAttributeName(key), value);
  }
  if (element?.hidden === true) add('hidden', true);
  if (element?.disabled === true) add('disabled', true);
  return attrs.join('');
}

function domBuilderElementHtml(element) {
  if (!element) return '';
  const tagName = String(element.localName || element.tagName || element.nodeName || 'div').toLowerCase();
  const childHtml = Array.from(element.children || []).map(child => domBuilderElementHtml(child)).join('');
  const body = childHtml || element.innerHTML || esc(element.textContent || '');
  return `<${tagName}${domBuilderSerializedAttributes(element)}>${body}</${tagName}>`;
}

function createToolbarButton(options = {}) {
  const dataset = {...(options.dataset || {})};
  if (options.action) dataset.action = options.action;
  const button = makeButton({
    type: options.type || 'button',
    id: options.id,
    className: options.className,
    role: options.role,
    html: options.html,
    label: options.label,
    disabled: options.disabled,
    title: options.title,
    ariaLabel: options.ariaLabel,
    pressed: options.pressed,
    checked: options.checked,
    dataset,
  });
  return setDomBuilderOptions(button, options);
}

function createActionRowItem(item = {}) {
  if (item.node) return item.node;
  if (item.kind === 'separator') {
    const separator = document.createElement(item.tagName || 'span');
    return setDomBuilderOptions(separator, {
      className: item.className,
      hidden: item.hidden,
      dataset: item.dataset,
      attributes: item.attributes,
      ariaHidden: item.ariaHidden !== false,
    });
  }
  if (item.kind === 'custom') {
    const node = document.createElement(item.tagName || 'span');
    return setDomBuilderOptions(node, item);
  }
  return createToolbarButton(item);
}

function createActionRow(options = {}) {
  const row = document.createElement(options.tagName || 'div');
  setDomBuilderOptions(row, options);
  for (const item of options.items || options.actions || []) {
    row.appendChild(createActionRowItem(item));
  }
  return row;
}

function createSegmentedControl(options = {}) {
  return createActionRow({
    tagName: options.tagName || 'span',
    className: options.className,
    role: options.role || 'group',
    ariaLabel: options.ariaLabel,
    hidden: options.hidden,
    dataset: options.dataset,
    attributes: options.attributes,
    actions: (options.items || []).map(item => ({
      ...item,
      className: item.className || options.buttonClassName || '',
      action: item.action || options.action,
    })),
  });
}

function toolbarButtonHtml(options = {}) {
  return domBuilderElementHtml(createToolbarButton(options));
}

function actionRowHtml(options = {}) {
  return domBuilderElementHtml(createActionRow(options));
}

function segmentedControlHtml(options = {}) {
  return domBuilderElementHtml(createSegmentedControl(options));
}

function bindActionDispatcher(parent, handlers = {}, options = {}) {
  return delegate(parent, options.type || 'click', options.selector || '[data-action]', async (event, target) => {
    const action = target?.dataset?.action || '';
    const handler = handlers[action];
    if (!handler || options.ignore?.(event, target) === true) return;
    if (options.preventDefault !== false) event.preventDefault();
    if (options.stopPropagation !== false) event.stopPropagation();
    if (options.skipDisabled !== false && (target.disabled || target.hidden)) return;
    await handler(event, target, action);
  }, options.listenerOptions || {});
}
