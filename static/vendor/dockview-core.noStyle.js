/**
 * dockview-core
 * @version 6.6.1
 * @link https://github.com/mathuo/dockview
 * @license MIT
 */
(function (global, factory) {
    typeof exports === 'object' && typeof module !== 'undefined' ? factory(exports) :
    typeof define === 'function' && define.amd ? define(['exports'], factory) :
    (global = typeof globalThis !== 'undefined' ? globalThis : global || self, factory(global["dockview-core"] = {}));
})(this, (function (exports) { 'use strict';

    class TransferObject {
    }
    class PanelTransfer extends TransferObject {
        constructor(viewId, groupId, panelId, tabGroupId) {
            super();
            this.viewId = viewId;
            this.groupId = groupId;
            this.panelId = panelId;
            this.tabGroupId = tabGroupId;
        }
    }
    class PaneTransfer extends TransferObject {
        constructor(viewId, paneId) {
            super();
            this.viewId = viewId;
            this.paneId = paneId;
        }
    }
    /**
     * A singleton to store transfer data during drag & drop operations that are only valid within the application.
     */
    class LocalSelectionTransfer {
        constructor() {
            // protect against external instantiation
        }
        static getInstance() {
            return LocalSelectionTransfer.INSTANCE;
        }
        hasData(proto) {
            return proto && proto === this.proto;
        }
        clearData(proto) {
            if (this.hasData(proto)) {
                this.proto = undefined;
                this.data = undefined;
            }
        }
        getData(proto) {
            if (this.hasData(proto)) {
                return this.data;
            }
            return undefined;
        }
        setData(data, proto) {
            if (proto) {
                this.data = data;
                this.proto = proto;
            }
        }
    }
    LocalSelectionTransfer.INSTANCE = new LocalSelectionTransfer();
    function getPanelData() {
        const panelTransfer = LocalSelectionTransfer.getInstance();
        const isPanelEvent = panelTransfer.hasData(PanelTransfer.prototype);
        if (!isPanelEvent) {
            return undefined;
        }
        return panelTransfer.getData(PanelTransfer.prototype)[0];
    }
    function getPaneData() {
        const paneTransfer = LocalSelectionTransfer.getInstance();
        const isPanelEvent = paneTransfer.hasData(PaneTransfer.prototype);
        if (!isPanelEvent) {
            return undefined;
        }
        return paneTransfer.getData(PaneTransfer.prototype)[0];
    }

    exports.DockviewDisposable = void 0;
    (function (Disposable) {
        Disposable.NONE = {
            dispose: () => {
                // noop
            },
        };
        function from(func) {
            return {
                dispose: () => {
                    func();
                },
            };
        }
        Disposable.from = from;
    })(exports.DockviewDisposable || (exports.DockviewDisposable = {}));
    class CompositeDisposable {
        get isDisposed() {
            return this._isDisposed;
        }
        constructor(...args) {
            this._isDisposed = false;
            this._disposables = new Set(args);
        }
        addDisposables(...args) {
            args.forEach((arg) => this._disposables.add(arg));
        }
        removeDisposable(disposable) {
            this._disposables.delete(disposable);
        }
        dispose() {
            if (this._isDisposed) {
                return;
            }
            this._isDisposed = true;
            this._disposables.forEach((arg) => arg.dispose());
            this._disposables.clear();
        }
    }
    class MutableDisposable {
        constructor() {
            this._disposable = exports.DockviewDisposable.NONE;
        }
        set value(disposable) {
            if (this._disposable) {
                this._disposable.dispose();
            }
            this._disposable = disposable;
        }
        dispose() {
            if (this._disposable) {
                this._disposable.dispose();
                this._disposable = exports.DockviewDisposable.NONE;
            }
        }
    }

    exports.DockviewEvent = void 0;
    (function (Event) {
        Event.any = (...children) => {
            return (listener) => {
                const disposables = children.map((child) => child(listener));
                return {
                    dispose: () => {
                        disposables.forEach((d) => {
                            d.dispose();
                        });
                    },
                };
            };
        };
    })(exports.DockviewEvent || (exports.DockviewEvent = {}));
    class DockviewEvent {
        constructor() {
            this._defaultPrevented = false;
        }
        get defaultPrevented() {
            return this._defaultPrevented;
        }
        preventDefault() {
            this._defaultPrevented = true;
        }
    }
    class AcceptableEvent {
        constructor() {
            this._isAccepted = false;
        }
        get isAccepted() {
            return this._isAccepted;
        }
        accept() {
            this._isAccepted = true;
        }
    }
    class LeakageMonitor {
        constructor() {
            this.events = new Map();
        }
        get size() {
            return this.events.size;
        }
        add(event, stacktrace) {
            this.events.set(event, stacktrace);
        }
        delete(event) {
            this.events.delete(event);
        }
        clear() {
            this.events.clear();
        }
    }
    class Stacktrace {
        static create() {
            var _a;
            return new Stacktrace((_a = new Error().stack) !== null && _a !== void 0 ? _a : '');
        }
        constructor(value) {
            this.value = value;
        }
        print() {
            console.warn('dockview: stacktrace', this.value);
        }
    }
    class Listener {
        constructor(callback, stacktrace) {
            this.callback = callback;
            this.stacktrace = stacktrace;
        }
    }
    // relatively simple event emitter taken from https://github.com/microsoft/vscode/blob/master/src/vs/base/common/event.ts
    class Emitter {
        static setLeakageMonitorEnabled(isEnabled) {
            if (isEnabled !== Emitter.ENABLE_TRACKING) {
                Emitter.MEMORY_LEAK_WATCHER.clear();
            }
            Emitter.ENABLE_TRACKING = isEnabled;
        }
        get value() {
            return this._last;
        }
        constructor(options) {
            this.options = options;
            this._listeners = [];
            this._disposed = false;
            this._pauseTokens = new Set();
        }
        get event() {
            if (!this._event) {
                this._event = (callback) => {
                    var _a;
                    if (((_a = this.options) === null || _a === void 0 ? void 0 : _a.replay) && this._last !== undefined) {
                        callback(this._last);
                    }
                    const listener = new Listener(callback, Emitter.ENABLE_TRACKING ? Stacktrace.create() : undefined);
                    this._listeners.push(listener);
                    return {
                        dispose: () => {
                            const index = this._listeners.indexOf(listener);
                            if (index > -1) {
                                this._listeners.splice(index, 1);
                            }
                            else if (Emitter.ENABLE_TRACKING) ;
                        },
                    };
                };
                if (Emitter.ENABLE_TRACKING) {
                    Emitter.MEMORY_LEAK_WATCHER.add(this._event, Stacktrace.create());
                }
            }
            return this._event;
        }
        fire(e) {
            var _a;
            if (this._pauseTokens.size > 0) {
                // while paused, the event is dropped entirely — `_last` is not
                // updated, so replay subscribers won't see values fired during a pause
                return;
            }
            if ((_a = this.options) === null || _a === void 0 ? void 0 : _a.replay) {
                this._last = e;
            }
            for (const listener of this._listeners) {
                listener.callback(e);
            }
        }
        pause() {
            const token = {};
            this._pauseTokens.add(token);
            return exports.DockviewDisposable.from(() => this._pauseTokens.delete(token));
        }
        dispose() {
            if (!this._disposed) {
                this._disposed = true;
                if (this._listeners.length > 0) {
                    if (Emitter.ENABLE_TRACKING) {
                        queueMicrotask(() => {
                            var _a;
                            // don't check until stack of execution is completed to allow for out-of-order disposals within the same execution block
                            for (const listener of this._listeners) {
                                console.warn('dockview: stacktrace', (_a = listener.stacktrace) === null || _a === void 0 ? void 0 : _a.print());
                            }
                        });
                    }
                    this._listeners = [];
                }
                if (Emitter.ENABLE_TRACKING && this._event) {
                    Emitter.MEMORY_LEAK_WATCHER.delete(this._event);
                }
            }
        }
    }
    Emitter.ENABLE_TRACKING = false;
    Emitter.MEMORY_LEAK_WATCHER = new LeakageMonitor();
    function addDisposableListener(element, type, listener, options) {
        element.addEventListener(type, listener, options);
        return {
            dispose: () => {
                element.removeEventListener(type, listener, options);
            },
        };
    }
    /**
     *
     * Event Emitter that fires events from a Microtask callback, only one event will fire per event-loop cycle.
     *
     * It's kind of like using an `asapScheduler` in RxJs with additional logic to only fire once per event-loop cycle.
     * This implementation exists to avoid external dependencies.
     *
     * @see https://developer.mozilla.org/en-US/docs/Web/API/queueMicrotask
     * @see https://rxjs.dev/api/index/const/asapScheduler
     */
    class AsapEvent {
        constructor() {
            this._onFired = new Emitter();
            this._currentFireCount = 0;
            this._queued = false;
            this.onEvent = (e) => {
                /**
                 * when the event is first subscribed to take note of the current fire count
                 */
                const fireCountAtTimeOfEventSubscription = this._currentFireCount;
                return this._onFired.event(() => {
                    /**
                     * if the current fire count is greater than the fire count at event subscription
                     * then the event has been fired since we subscribed and it's ok to "on_next" the event.
                     *
                     * if the count is not greater then what we are recieving is an event from the microtask
                     * queue that was triggered before we actually subscribed and therfore we should ignore it.
                     */
                    if (this._currentFireCount > fireCountAtTimeOfEventSubscription) {
                        e();
                    }
                });
            };
        }
        fire() {
            this._currentFireCount++;
            if (this._queued) {
                return;
            }
            this._queued = true;
            queueMicrotask(() => {
                this._queued = false;
                this._onFired.fire();
            });
        }
        dispose() {
            this._onFired.dispose();
        }
    }

    class OverflowObserver extends CompositeDisposable {
        constructor(el) {
            super();
            this._onDidChange = new Emitter();
            this.onDidChange = this._onDidChange.event;
            this._value = null;
            this.addDisposables(this._onDidChange, watchElementResize(el, (entry) => {
                const hasScrollX = entry.target.scrollWidth > entry.target.clientWidth;
                const hasScrollY = entry.target.scrollHeight > entry.target.clientHeight;
                this._value = { hasScrollX, hasScrollY };
                this._onDidChange.fire(this._value);
            }));
        }
    }
    function watchElementResize(element, cb) {
        const observer = new ResizeObserver((entires) => {
            /**
             * Fast browser window resize produces Error: ResizeObserver loop limit exceeded.
             * The error isn't visible in browser console, doesn't affect functionality, but degrades performance.
             * See https://stackoverflow.com/questions/49384120/resizeobserver-loop-limit-exceeded/58701523#58701523
             */
            requestAnimationFrame(() => {
                const firstEntry = entires[0];
                cb(firstEntry);
            });
        });
        observer.observe(element);
        return {
            dispose: () => {
                observer.unobserve(element);
                observer.disconnect();
            },
        };
    }
    const removeClasses = (element, ...classes) => {
        for (const classname of classes) {
            if (element.classList.contains(classname)) {
                element.classList.remove(classname);
            }
        }
    };
    const addClasses = (element, ...classes) => {
        for (const classname of classes) {
            if (!element.classList.contains(classname)) {
                element.classList.add(classname);
            }
        }
    };
    const toggleClass = (element, className, isToggled) => {
        const hasClass = element.classList.contains(className);
        if (isToggled && !hasClass) {
            element.classList.add(className);
        }
        if (!isToggled && hasClass) {
            element.classList.remove(className);
        }
    };
    function isAncestor(testChild, testAncestor) {
        while (testChild) {
            if (testChild === testAncestor) {
                return true;
            }
            testChild = testChild.parentNode;
        }
        return false;
    }
    function trackFocus(element) {
        return new FocusTracker(element);
    }
    /**
     * Track focus on an element. Ensure tabIndex is set when an HTMLElement is not focusable by default
     */
    class FocusTracker extends CompositeDisposable {
        constructor(element) {
            super();
            this._onDidFocus = new Emitter();
            this.onDidFocus = this._onDidFocus.event;
            this._onDidBlur = new Emitter();
            this.onDidBlur = this._onDidBlur.event;
            this.addDisposables(this._onDidFocus, this._onDidBlur);
            let hasFocus = isAncestor(document.activeElement, element);
            let loosingFocus = false;
            const onFocus = () => {
                loosingFocus = false;
                if (!hasFocus) {
                    hasFocus = true;
                    this._onDidFocus.fire();
                }
            };
            const onBlur = () => {
                if (hasFocus) {
                    loosingFocus = true;
                    window.setTimeout(() => {
                        if (loosingFocus) {
                            loosingFocus = false;
                            hasFocus = false;
                            this._onDidBlur.fire();
                        }
                    }, 0);
                }
            };
            this._refreshStateHandler = () => {
                const currentNodeHasFocus = isAncestor(document.activeElement, element);
                if (currentNodeHasFocus !== hasFocus) {
                    if (hasFocus) {
                        onBlur();
                    }
                    else {
                        onFocus();
                    }
                }
            };
            this.addDisposables(addDisposableListener(element, 'focus', onFocus, true));
            this.addDisposables(addDisposableListener(element, 'blur', onBlur, true));
        }
        refreshState() {
            this._refreshStateHandler();
        }
    }
    // quasi: apparently, but not really; seemingly
    const QUASI_PREVENT_DEFAULT_KEY = 'dv-quasiPreventDefault';
    // mark an event directly for other listeners to check
    function quasiPreventDefault(event) {
        event[QUASI_PREVENT_DEFAULT_KEY] = true;
    }
    // check if this event has been marked
    function quasiDefaultPrevented(event) {
        return event[QUASI_PREVENT_DEFAULT_KEY];
    }
    function addStyles(document, styleSheetList, options = {}) {
        const styleSheets = Array.from(styleSheetList);
        const { nonce } = options;
        const resolvedNonce = typeof nonce === 'function' ? nonce(document) : nonce;
        for (const styleSheet of styleSheets) {
            if (styleSheet.href) {
                const link = document.createElement('link');
                link.href = styleSheet.href;
                link.type = styleSheet.type;
                link.rel = 'stylesheet';
                document.head.appendChild(link);
                // The <link> will load and apply its rules in the target
                // document. Reading cssRules here would duplicate them
                // (and throws for cross-origin sheets).
                continue;
            }
            let cssTexts = [];
            try {
                if (styleSheet.cssRules) {
                    cssTexts = Array.from(styleSheet.cssRules).map((rule) => rule.cssText);
                }
            }
            catch (err) {
                console.warn('dockview: failed to access stylesheet rules due to security restrictions', err);
            }
            const fragment = document.createDocumentFragment();
            for (const rule of cssTexts) {
                const style = document.createElement('style');
                if (resolvedNonce) {
                    style.setAttribute('nonce', resolvedNonce);
                }
                style.appendChild(document.createTextNode(rule));
                fragment.appendChild(style);
            }
            document.head.appendChild(fragment);
        }
    }
    function getDomNodePagePosition(domNode) {
        const { left, top, width, height } = domNode.getBoundingClientRect();
        return {
            left: left + window.scrollX,
            top: top + window.scrollY,
            width: width,
            height: height,
        };
    }
    /**
     * Check whether an element is in the DOM (including the Shadow DOM)
     * @see https://terodox.tech/how-to-tell-if-an-element-is-in-the-dom-including-the-shadow-dom/
     */
    function isInDocument(element) {
        let currentElement = element;
        while (currentElement === null || currentElement === void 0 ? void 0 : currentElement.parentNode) {
            if (currentElement.parentNode === document) {
                return true;
            }
            else if (currentElement.parentNode instanceof DocumentFragment) {
                // handle shadow DOMs
                currentElement = currentElement.parentNode.host;
            }
            else {
                currentElement = currentElement.parentNode;
            }
        }
        return false;
    }
    function addTestId(element, id) {
        element.setAttribute('data-testid', id);
    }
    /**
     * Should be more efficient than element.querySelectorAll("*") since there
     * is no need to store every element in-memory using this approach
     */
    function allTagsNamesInclusiveOfShadowDoms(tagNames, rootNode) {
        const iframes = [];
        function findIframesInNode(node) {
            if (node.nodeType === Node.ELEMENT_NODE) {
                if (tagNames.includes(node.tagName)) {
                    iframes.push(node);
                }
                if (node.shadowRoot) {
                    findIframesInNode(node.shadowRoot);
                }
                for (const child of node.children) {
                    findIframesInNode(child);
                }
            }
        }
        // Document → walk from its root element. Element → walk from itself.
        const startEl = rootNode instanceof Document
            ? rootNode.documentElement
            : rootNode;
        findIframesInNode(startEl);
        return iframes;
    }
    function disableIframePointEvents(rootNode = document) {
        const iframes = allTagsNamesInclusiveOfShadowDoms(['IFRAME', 'WEBVIEW'], rootNode);
        const original = new WeakMap(); // don't hold onto HTMLElement references longer than required
        for (const iframe of iframes) {
            original.set(iframe, iframe.style.pointerEvents);
            iframe.style.pointerEvents = 'none';
        }
        return {
            release: () => {
                var _a;
                for (const iframe of iframes) {
                    iframe.style.pointerEvents = (_a = original.get(iframe)) !== null && _a !== void 0 ? _a : 'auto';
                }
                iframes.splice(0, iframes.length); // don't hold onto HTMLElement references longer than required
            },
        };
    }
    function getDockviewTheme(element) {
        function toClassList(element) {
            const list = [];
            for (let i = 0; i < element.classList.length; i++) {
                list.push(element.classList.item(i));
            }
            return list;
        }
        let theme = undefined;
        let parent = element;
        while (parent !== null) {
            theme = toClassList(parent).find((cls) => cls.startsWith('dockview-theme-'));
            if (typeof theme === 'string') {
                break;
            }
            parent = parent.parentElement;
        }
        return theme;
    }
    class Classnames {
        constructor(element) {
            this.element = element;
            this._classNames = [];
        }
        setClassNames(classNames) {
            for (const className of this._classNames) {
                toggleClass(this.element, className, false);
            }
            this._classNames = classNames
                .split(' ')
                .filter((v) => v.trim().length > 0);
            for (const className of this._classNames) {
                toggleClass(this.element, className, true);
            }
        }
    }
    const DEBOUCE_DELAY = 100;
    function isChildEntirelyVisibleWithinParent(child, parent) {
        const childPosition = getDomNodePagePosition(child);
        const parentPosition = getDomNodePagePosition(parent);
        // Check horizontal visibility
        if (childPosition.left < parentPosition.left) {
            return false;
        }
        if (childPosition.left + childPosition.width >
            parentPosition.left + parentPosition.width) {
            return false;
        }
        // Check vertical visibility
        if (childPosition.top < parentPosition.top) {
            return false;
        }
        if (childPosition.top + childPosition.height >
            parentPosition.top + parentPosition.height) {
            return false;
        }
        return true;
    }
    function onDidWindowMoveEnd(window) {
        const emitter = new Emitter();
        let previousScreenX = window.screenX;
        let previousScreenY = window.screenY;
        let timeout;
        const checkMovement = () => {
            if (window.closed) {
                return;
            }
            const currentScreenX = window.screenX;
            const currentScreenY = window.screenY;
            if (currentScreenX !== previousScreenX ||
                currentScreenY !== previousScreenY) {
                clearTimeout(timeout);
                timeout = setTimeout(() => {
                    emitter.fire();
                }, DEBOUCE_DELAY);
                previousScreenX = currentScreenX;
                previousScreenY = currentScreenY;
            }
            requestAnimationFrame(checkMovement);
        };
        checkMovement();
        return emitter;
    }
    function onDidWindowResizeEnd(element, cb) {
        let resizeTimeout;
        const disposable = new CompositeDisposable(addDisposableListener(element, 'resize', () => {
            clearTimeout(resizeTimeout);
            resizeTimeout = setTimeout(() => {
                cb();
            }, DEBOUCE_DELAY);
        }));
        return disposable;
    }
    function shiftAbsoluteElementIntoView(element, root, options = { buffer: 10 }) {
        const buffer = options.buffer;
        const rect = element.getBoundingClientRect();
        const rootRect = root.getBoundingClientRect();
        let translateX = 0;
        let translateY = 0;
        const left = rect.left - rootRect.left;
        const top = rect.top - rootRect.top;
        const bottom = rect.bottom - rootRect.bottom;
        const right = rect.right - rootRect.right;
        // Check horizontal overflow
        if (left < buffer) {
            translateX = buffer - left;
        }
        else if (right > buffer) {
            translateX = -buffer - right;
        }
        // Check vertical overflow
        if (top < buffer) {
            translateY = buffer - top;
        }
        else if (bottom > buffer) {
            translateY = -bottom - buffer;
        }
        // Apply the translation if needed
        if (translateX !== 0 || translateY !== 0) {
            element.style.transform = `translate(${translateX}px, ${translateY}px)`;
        }
    }
    function findRelativeZIndexParent(el) {
        let tmp = el;
        while (tmp && (tmp.style.zIndex === 'auto' || tmp.style.zIndex === '')) {
            tmp = tmp.parentElement;
        }
        return tmp;
    }

    function tail(arr) {
        if (arr.length === 0) {
            throw new Error('Invalid tail call');
        }
        return [arr.slice(0, arr.length - 1), arr[arr.length - 1]];
    }
    function sequenceEquals(arr1, arr2) {
        if (arr1.length !== arr2.length) {
            return false;
        }
        for (let i = 0; i < arr1.length; i++) {
            if (arr1[i] !== arr2[i]) {
                return false;
            }
        }
        return true;
    }
    /**
     * Pushes an element to the start of the array, if found.
     */
    function pushToStart(arr, value) {
        const index = arr.indexOf(value);
        if (index > -1) {
            arr.splice(index, 1);
            arr.unshift(value);
        }
    }
    /**
     * Pushes an element to the end of the array, if found.
     */
    function pushToEnd(arr, value) {
        const index = arr.indexOf(value);
        if (index > -1) {
            arr.splice(index, 1);
            arr.push(value);
        }
    }
    function firstIndex(array, fn) {
        for (let i = 0; i < array.length; i++) {
            const element = array[i];
            if (fn(element)) {
                return i;
            }
        }
        return -1;
    }
    function remove(array, value) {
        const index = array.findIndex((t) => t === value);
        if (index > -1) {
            array.splice(index, 1);
            return true;
        }
        return false;
    }

    const clamp = (value, min, max) => {
        if (min > max) {
            /**
             * caveat: an error should be thrown here if this was a proper `clamp` function but we need to handle
             * cases where `min` > `max` and in those cases return `min`.
             */
            return min;
        }
        return Math.min(max, Math.max(value, min));
    };
    const sequentialNumberGenerator = () => {
        let value = 1;
        return { next: () => (value++).toString() };
    };
    const range = (from, to) => {
        const result = [];
        if (typeof to !== 'number') {
            to = from;
            from = 0;
        }
        if (from <= to) {
            for (let i = from; i < to; i++) {
                result.push(i);
            }
        }
        else {
            for (let i = from; i > to; i--) {
                result.push(i);
            }
        }
        return result;
    };

    class ViewItem {
        set size(size) {
            this._size = size;
        }
        get size() {
            return this._size;
        }
        get cachedVisibleSize() {
            return this._cachedVisibleSize;
        }
        get visible() {
            return typeof this._cachedVisibleSize === 'undefined';
        }
        get minimumSize() {
            return this.visible ? this.view.minimumSize : 0;
        }
        get viewMinimumSize() {
            return this.view.minimumSize;
        }
        get maximumSize() {
            return this.visible ? this.view.maximumSize : 0;
        }
        get viewMaximumSize() {
            return this.view.maximumSize;
        }
        get priority() {
            return this.view.priority;
        }
        get snap() {
            return !!this.view.snap;
        }
        set enabled(enabled) {
            this.container.style.pointerEvents = enabled ? '' : 'none';
        }
        constructor(container, view, size, disposable) {
            this.container = container;
            this.view = view;
            this.disposable = disposable;
            this._cachedVisibleSize = undefined;
            if (typeof size === 'number') {
                this._size = size;
                this._cachedVisibleSize = undefined;
                container.classList.add('visible');
            }
            else {
                this._size = 0;
                this._cachedVisibleSize = size.cachedVisibleSize;
            }
        }
        setVisible(visible, size) {
            var _a;
            if (visible === this.visible) {
                return;
            }
            if (visible) {
                this.size = clamp((_a = this._cachedVisibleSize) !== null && _a !== void 0 ? _a : 0, this.viewMinimumSize, this.viewMaximumSize);
                this._cachedVisibleSize = undefined;
            }
            else {
                this._cachedVisibleSize =
                    typeof size === 'number' ? size : this.size;
                this.size = 0;
            }
            this.container.classList.toggle('visible', visible);
            if (this.view.setVisible) {
                this.view.setVisible(visible);
            }
        }
        dispose() {
            this.disposable.dispose();
            return this.view;
        }
    }

    /*---------------------------------------------------------------------------------------------
     * Accreditation: This file is largly based upon the MIT licenced VSCode sourcecode found at:
     * https://github.com/microsoft/vscode/tree/main/src/vs/base/browser/ui/splitview
     *--------------------------------------------------------------------------------------------*/
    exports.Orientation = void 0;
    (function (Orientation) {
        Orientation["HORIZONTAL"] = "HORIZONTAL";
        Orientation["VERTICAL"] = "VERTICAL";
    })(exports.Orientation || (exports.Orientation = {}));
    exports.SashState = void 0;
    (function (SashState) {
        SashState[SashState["MAXIMUM"] = 0] = "MAXIMUM";
        SashState[SashState["MINIMUM"] = 1] = "MINIMUM";
        SashState[SashState["DISABLED"] = 2] = "DISABLED";
        SashState[SashState["ENABLED"] = 3] = "ENABLED";
    })(exports.SashState || (exports.SashState = {}));
    exports.LayoutPriority = void 0;
    (function (LayoutPriority) {
        LayoutPriority["Low"] = "low";
        LayoutPriority["High"] = "high";
        LayoutPriority["Normal"] = "normal";
    })(exports.LayoutPriority || (exports.LayoutPriority = {}));
    exports.Sizing = void 0;
    (function (Sizing) {
        Sizing.Distribute = { type: 'distribute' };
        function Split(index) {
            return { type: 'split', index };
        }
        Sizing.Split = Split;
        function Invisible(cachedVisibleSize) {
            return { type: 'invisible', cachedVisibleSize };
        }
        Sizing.Invisible = Invisible;
    })(exports.Sizing || (exports.Sizing = {}));
    class Splitview {
        get contentSize() {
            return this._contentSize;
        }
        get size() {
            return this._size;
        }
        set size(value) {
            this._size = value;
        }
        get orthogonalSize() {
            return this._orthogonalSize;
        }
        set orthogonalSize(value) {
            this._orthogonalSize = value;
        }
        get length() {
            return this.viewItems.length;
        }
        get proportions() {
            return this._proportions ? [...this._proportions] : undefined;
        }
        get orientation() {
            return this._orientation;
        }
        set orientation(value) {
            this._orientation = value;
            const tmp = this.size;
            this.size = this.orthogonalSize;
            this.orthogonalSize = tmp;
            removeClasses(this.element, 'dv-horizontal', 'dv-vertical');
            this.element.classList.add(this.orientation == exports.Orientation.HORIZONTAL
                ? 'dv-horizontal'
                : 'dv-vertical');
        }
        get minimumSize() {
            return this.viewItems.reduce((r, item) => r + item.minimumSize, 0);
        }
        get maximumSize() {
            return this.length === 0
                ? Number.POSITIVE_INFINITY
                : this.viewItems.reduce((r, item) => r + item.maximumSize, 0);
        }
        get startSnappingEnabled() {
            return this._startSnappingEnabled;
        }
        set startSnappingEnabled(startSnappingEnabled) {
            if (this._startSnappingEnabled === startSnappingEnabled) {
                return;
            }
            this._startSnappingEnabled = startSnappingEnabled;
            this.updateSashEnablement();
        }
        get endSnappingEnabled() {
            return this._endSnappingEnabled;
        }
        set endSnappingEnabled(endSnappingEnabled) {
            if (this._endSnappingEnabled === endSnappingEnabled) {
                return;
            }
            this._endSnappingEnabled = endSnappingEnabled;
            this.updateSashEnablement();
        }
        get disabled() {
            return this._disabled;
        }
        set disabled(value) {
            this._disabled = value;
            toggleClass(this.element, 'dv-splitview-disabled', value);
        }
        get margin() {
            return this._margin;
        }
        set margin(value) {
            this._margin = value;
            toggleClass(this.element, 'dv-splitview-has-margin', value !== 0);
        }
        constructor(container, options) {
            var _a, _b;
            this.container = container;
            this.viewItems = [];
            this.sashes = [];
            this._size = 0;
            this._orthogonalSize = 0;
            this._contentSize = 0;
            this._proportions = undefined;
            this._startSnappingEnabled = true;
            this._endSnappingEnabled = true;
            this._disabled = false;
            this._margin = 0;
            this._onDidSashEnd = new Emitter();
            this.onDidSashEnd = this._onDidSashEnd.event;
            this._onDidAddView = new Emitter();
            this.onDidAddView = this._onDidAddView.event;
            this._onDidRemoveView = new Emitter();
            this.onDidRemoveView = this._onDidRemoveView.event;
            this.resize = (index, delta, sizes = this.viewItems.map((x) => x.size), lowPriorityIndexes, highPriorityIndexes, overloadMinDelta = Number.NEGATIVE_INFINITY, overloadMaxDelta = Number.POSITIVE_INFINITY, snapBefore, snapAfter) => {
                if (index < 0 || index > this.viewItems.length) {
                    return 0;
                }
                const upIndexes = range(index, -1);
                const downIndexes = range(index + 1, this.viewItems.length);
                //
                if (highPriorityIndexes) {
                    for (const i of highPriorityIndexes) {
                        pushToStart(upIndexes, i);
                        pushToStart(downIndexes, i);
                    }
                }
                if (lowPriorityIndexes) {
                    for (const i of lowPriorityIndexes) {
                        pushToEnd(upIndexes, i);
                        pushToEnd(downIndexes, i);
                    }
                }
                //
                const upItems = upIndexes.map((i) => this.viewItems[i]);
                const upSizes = upIndexes.map((i) => sizes[i]);
                //
                const downItems = downIndexes.map((i) => this.viewItems[i]);
                const downSizes = downIndexes.map((i) => sizes[i]);
                //
                const minDeltaUp = upIndexes.reduce((_, i) => _ + this.viewItems[i].minimumSize - sizes[i], 0);
                const maxDeltaUp = upIndexes.reduce((_, i) => _ + this.viewItems[i].maximumSize - sizes[i], 0);
                //
                const maxDeltaDown = downIndexes.length === 0
                    ? Number.POSITIVE_INFINITY
                    : downIndexes.reduce((_, i) => _ + sizes[i] - this.viewItems[i].minimumSize, 0);
                const minDeltaDown = downIndexes.length === 0
                    ? Number.NEGATIVE_INFINITY
                    : downIndexes.reduce((_, i) => _ + sizes[i] - this.viewItems[i].maximumSize, 0);
                //
                const minDelta = Math.max(minDeltaUp, minDeltaDown);
                const maxDelta = Math.min(maxDeltaDown, maxDeltaUp);
                //
                let snapped = false;
                if (snapBefore) {
                    const snapView = this.viewItems[snapBefore.index];
                    const visible = delta >= snapBefore.limitDelta;
                    snapped = visible !== snapView.visible;
                    snapView.setVisible(visible, snapBefore.size);
                }
                if (!snapped && snapAfter) {
                    const snapView = this.viewItems[snapAfter.index];
                    const visible = delta < snapAfter.limitDelta;
                    snapped = visible !== snapView.visible;
                    snapView.setVisible(visible, snapAfter.size);
                }
                if (snapped) {
                    return this.resize(index, delta, sizes, lowPriorityIndexes, highPriorityIndexes, overloadMinDelta, overloadMaxDelta);
                }
                //
                const tentativeDelta = clamp(delta, minDelta, maxDelta);
                let actualDelta = 0;
                //
                let deltaUp = tentativeDelta;
                for (let i = 0; i < upItems.length; i++) {
                    const item = upItems[i];
                    const size = clamp(upSizes[i] + deltaUp, item.minimumSize, item.maximumSize);
                    const viewDelta = size - upSizes[i];
                    actualDelta += viewDelta;
                    deltaUp -= viewDelta;
                    item.size = size;
                }
                //
                let deltaDown = actualDelta;
                for (let i = 0; i < downItems.length; i++) {
                    const item = downItems[i];
                    const size = clamp(downSizes[i] - deltaDown, item.minimumSize, item.maximumSize);
                    const viewDelta = size - downSizes[i];
                    deltaDown += viewDelta;
                    item.size = size;
                }
                //
                return delta;
            };
            this._orientation = (_a = options.orientation) !== null && _a !== void 0 ? _a : exports.Orientation.VERTICAL;
            this.element = this.createContainer();
            this.margin = (_b = options.margin) !== null && _b !== void 0 ? _b : 0;
            this.proportionalLayout =
                options.proportionalLayout === undefined
                    ? true
                    : !!options.proportionalLayout;
            this.viewContainer = this.createViewContainer();
            this.sashContainer = this.createSashContainer();
            this.element.appendChild(this.sashContainer);
            this.element.appendChild(this.viewContainer);
            this.container.appendChild(this.element);
            this.style(options.styles);
            // We have an existing set of view, add them now
            if (options.descriptor) {
                this._size = options.descriptor.size;
                options.descriptor.views.forEach((viewDescriptor, index) => {
                    const sizing = viewDescriptor.visible === undefined ||
                        viewDescriptor.visible
                        ? viewDescriptor.size
                        : {
                            type: 'invisible',
                            cachedVisibleSize: viewDescriptor.size,
                        };
                    const view = viewDescriptor.view;
                    this.addView(view, sizing, index, true
                    // true skip layout
                    );
                });
                // Initialize content size and proportions for first layout
                this._contentSize = this.viewItems.reduce((r, i) => r + i.size, 0);
                this.saveProportions();
            }
        }
        style(styles) {
            if ((styles === null || styles === void 0 ? void 0 : styles.separatorBorder) === 'transparent') {
                removeClasses(this.element, 'dv-separator-border');
                this.element.style.removeProperty('--dv-separator-border');
            }
            else {
                addClasses(this.element, 'dv-separator-border');
                if (styles === null || styles === void 0 ? void 0 : styles.separatorBorder) {
                    this.element.style.setProperty('--dv-separator-border', styles.separatorBorder);
                }
            }
        }
        isViewVisible(index) {
            if (index < 0 || index >= this.viewItems.length) {
                throw new Error('Index out of bounds');
            }
            const viewItem = this.viewItems[index];
            return viewItem.visible;
        }
        setViewVisible(index, visible) {
            if (index < 0 || index >= this.viewItems.length) {
                throw new Error('Index out of bounds');
            }
            const viewItem = this.viewItems[index];
            viewItem.setVisible(visible, viewItem.size);
            this.distributeEmptySpace(index);
            this.layoutViews();
            this.saveProportions();
        }
        getViewSize(index) {
            if (index < 0 || index >= this.viewItems.length) {
                return -1;
            }
            return this.viewItems[index].size;
        }
        resizeView(index, size) {
            if (index < 0 || index >= this.viewItems.length) {
                return;
            }
            const indexes = range(this.viewItems.length).filter((i) => i !== index);
            const lowPriorityIndexes = [
                ...indexes.filter((i) => this.viewItems[i].priority === exports.LayoutPriority.Low),
                index,
            ];
            const highPriorityIndexes = indexes.filter((i) => this.viewItems[i].priority === exports.LayoutPriority.High);
            const item = this.viewItems[index];
            size = Math.round(size);
            size = clamp(size, item.minimumSize, Math.min(item.maximumSize, this._size));
            item.size = size;
            this.relayout(lowPriorityIndexes, highPriorityIndexes);
        }
        getViews() {
            return this.viewItems.map((x) => x.view);
        }
        onDidChange(item, size) {
            const index = this.viewItems.indexOf(item);
            if (index < 0 || index >= this.viewItems.length) {
                return;
            }
            size = typeof size === 'number' ? size : item.size;
            size = clamp(size, item.minimumSize, item.maximumSize);
            item.size = size;
            const indexes = range(this.viewItems.length).filter((i) => i !== index);
            const lowPriorityIndexes = [
                ...indexes.filter((i) => this.viewItems[i].priority === exports.LayoutPriority.Low),
                index,
            ];
            const highPriorityIndexes = indexes.filter((i) => this.viewItems[i].priority === exports.LayoutPriority.High);
            /**
             * add this view we are changing to the low-index list since we have determined the size
             * here and don't want it changed
             */
            this.relayout([...lowPriorityIndexes, index], highPriorityIndexes);
        }
        addView(view, size = { type: 'distribute' }, index = this.viewItems.length, skipLayout) {
            const container = document.createElement('div');
            container.className = 'dv-view';
            container.appendChild(view.element);
            let viewSize;
            if (typeof size === 'number') {
                viewSize = size;
            }
            else if (size.type === 'split') {
                viewSize = this.getViewSize(size.index) / 2;
            }
            else if (size.type === 'invisible') {
                viewSize = { cachedVisibleSize: size.cachedVisibleSize };
            }
            else {
                viewSize = view.minimumSize;
            }
            const disposable = view.onDidChange((newSize) => this.onDidChange(viewItem, newSize.size));
            const viewItem = new ViewItem(container, view, viewSize, {
                dispose: () => {
                    disposable.dispose();
                    this.viewContainer.removeChild(container);
                },
            });
            if (index === this.viewItems.length) {
                this.viewContainer.appendChild(container);
            }
            else {
                this.viewContainer.insertBefore(container, this.viewContainer.children.item(index));
            }
            this.viewItems.splice(index, 0, viewItem);
            if (this.viewItems.length > 1) {
                //add sash
                const sash = document.createElement('div');
                sash.className = 'dv-sash';
                const onPointerStart = (event) => {
                    for (const item of this.viewItems) {
                        item.enabled = false;
                    }
                    const iframes = disableIframePointEvents();
                    const start = this._orientation === exports.Orientation.HORIZONTAL
                        ? event.clientX
                        : event.clientY;
                    const sashIndex = firstIndex(this.sashes, (s) => s.container === sash);
                    //
                    const sizes = this.viewItems.map((x) => x.size);
                    //
                    let snapBefore;
                    let snapAfter;
                    const upIndexes = range(sashIndex, -1);
                    const downIndexes = range(sashIndex + 1, this.viewItems.length);
                    const minDeltaUp = upIndexes.reduce((r, i) => r + (this.viewItems[i].minimumSize - sizes[i]), 0);
                    const maxDeltaUp = upIndexes.reduce((r, i) => r + (this.viewItems[i].viewMaximumSize - sizes[i]), 0);
                    const maxDeltaDown = downIndexes.length === 0
                        ? Number.POSITIVE_INFINITY
                        : downIndexes.reduce((r, i) => r +
                            (sizes[i] - this.viewItems[i].minimumSize), 0);
                    const minDeltaDown = downIndexes.length === 0
                        ? Number.NEGATIVE_INFINITY
                        : downIndexes.reduce((r, i) => r +
                            (sizes[i] -
                                this.viewItems[i].viewMaximumSize), 0);
                    const minDelta = Math.max(minDeltaUp, minDeltaDown);
                    const maxDelta = Math.min(maxDeltaDown, maxDeltaUp);
                    const snapBeforeIndex = this.findFirstSnapIndex(upIndexes);
                    const snapAfterIndex = this.findFirstSnapIndex(downIndexes);
                    if (typeof snapBeforeIndex === 'number') {
                        const snappedViewItem = this.viewItems[snapBeforeIndex];
                        const halfSize = Math.floor(snappedViewItem.viewMinimumSize / 2);
                        snapBefore = {
                            index: snapBeforeIndex,
                            limitDelta: snappedViewItem.visible
                                ? minDelta - halfSize
                                : minDelta + halfSize,
                            size: snappedViewItem.size,
                        };
                    }
                    if (typeof snapAfterIndex === 'number') {
                        const snappedViewItem = this.viewItems[snapAfterIndex];
                        const halfSize = Math.floor(snappedViewItem.viewMinimumSize / 2);
                        snapAfter = {
                            index: snapAfterIndex,
                            limitDelta: snappedViewItem.visible
                                ? maxDelta + halfSize
                                : maxDelta - halfSize,
                            size: snappedViewItem.size,
                        };
                    }
                    const onPointerMove = (event) => {
                        const current = this._orientation === exports.Orientation.HORIZONTAL
                            ? event.clientX
                            : event.clientY;
                        const delta = current - start;
                        this.resize(sashIndex, delta, sizes, undefined, undefined, minDelta, maxDelta, snapBefore, snapAfter);
                        this.distributeEmptySpace();
                        this.layoutViews();
                    };
                    const end = () => {
                        for (const item of this.viewItems) {
                            item.enabled = true;
                        }
                        iframes.release();
                        this.saveProportions();
                        document.removeEventListener('pointermove', onPointerMove);
                        document.removeEventListener('pointerup', end);
                        document.removeEventListener('pointercancel', end);
                        document.removeEventListener('contextmenu', end);
                        this._onDidSashEnd.fire(undefined);
                    };
                    document.addEventListener('pointermove', onPointerMove);
                    document.addEventListener('pointerup', end);
                    document.addEventListener('pointercancel', end);
                    document.addEventListener('contextmenu', end);
                };
                sash.addEventListener('pointerdown', onPointerStart);
                const sashItem = {
                    container: sash,
                    disposable: () => {
                        sash.removeEventListener('pointerdown', onPointerStart);
                        this.sashContainer.removeChild(sash);
                    },
                };
                this.sashContainer.appendChild(sash);
                this.sashes.push(sashItem);
            }
            if (!skipLayout) {
                this.relayout([index]);
            }
            if (!skipLayout &&
                typeof size !== 'number' &&
                size.type === 'distribute') {
                this.distributeViewSizes();
            }
            this._onDidAddView.fire(view);
        }
        distributeViewSizes() {
            const flexibleViewItems = [];
            let flexibleSize = 0;
            for (const item of this.viewItems) {
                if (item.maximumSize - item.minimumSize > 0) {
                    flexibleViewItems.push(item);
                    flexibleSize += item.size;
                }
            }
            const size = Math.floor(flexibleSize / flexibleViewItems.length);
            for (const item of flexibleViewItems) {
                item.size = clamp(size, item.minimumSize, item.maximumSize);
            }
            const indexes = range(this.viewItems.length);
            const lowPriorityIndexes = indexes.filter((i) => this.viewItems[i].priority === exports.LayoutPriority.Low);
            const highPriorityIndexes = indexes.filter((i) => this.viewItems[i].priority === exports.LayoutPriority.High);
            this.relayout(lowPriorityIndexes, highPriorityIndexes);
        }
        removeView(index, sizing, skipLayout = false) {
            // Remove view
            const viewItem = this.viewItems.splice(index, 1)[0];
            viewItem.dispose();
            // Remove sash
            if (this.viewItems.length >= 1) {
                const sashIndex = Math.max(index - 1, 0);
                const sashItem = this.sashes.splice(sashIndex, 1)[0];
                sashItem.disposable();
            }
            if (!skipLayout) {
                this.relayout();
            }
            if (sizing && sizing.type === 'distribute') {
                this.distributeViewSizes();
            }
            this._onDidRemoveView.fire(viewItem.view);
            return viewItem.view;
        }
        getViewCachedVisibleSize(index) {
            if (index < 0 || index >= this.viewItems.length) {
                throw new Error('Index out of bounds');
            }
            const viewItem = this.viewItems[index];
            return viewItem.cachedVisibleSize;
        }
        moveView(from, to) {
            const cachedVisibleSize = this.getViewCachedVisibleSize(from);
            const sizing = typeof cachedVisibleSize === 'undefined'
                ? this.getViewSize(from)
                : exports.Sizing.Invisible(cachedVisibleSize);
            const view = this.removeView(from, undefined, true);
            this.addView(view, sizing, to);
        }
        layout(size, orthogonalSize) {
            const previousSize = Math.max(this.size, this._contentSize);
            this.size = size;
            this.orthogonalSize = orthogonalSize;
            if (!this.proportions) {
                const indexes = range(this.viewItems.length);
                const lowPriorityIndexes = indexes.filter((i) => this.viewItems[i].priority === exports.LayoutPriority.Low);
                const highPriorityIndexes = indexes.filter((i) => this.viewItems[i].priority === exports.LayoutPriority.High);
                this.resize(this.viewItems.length - 1, size - previousSize, undefined, lowPriorityIndexes, highPriorityIndexes);
            }
            else {
                let total = 0;
                for (let i = 0; i < this.viewItems.length; i++) {
                    const item = this.viewItems[i];
                    const proportion = this.proportions[i];
                    if (typeof proportion === 'number') {
                        total += proportion;
                    }
                    else {
                        size -= item.size;
                    }
                }
                for (let i = 0; i < this.viewItems.length; i++) {
                    const item = this.viewItems[i];
                    const proportion = this.proportions[i];
                    if (typeof proportion === 'number' && total > 0) {
                        item.size = clamp(Math.round((proportion * size) / total), item.minimumSize, item.maximumSize);
                    }
                }
            }
            this.distributeEmptySpace();
            this.layoutViews();
        }
        relayout(lowPriorityIndexes, highPriorityIndexes) {
            const contentSize = this.viewItems.reduce((r, i) => r + i.size, 0);
            this.resize(this.viewItems.length - 1, this._size - contentSize, undefined, lowPriorityIndexes, highPriorityIndexes);
            this.distributeEmptySpace();
            this.layoutViews();
            this.saveProportions();
        }
        distributeEmptySpace(lowPriorityIndex) {
            const contentSize = this.viewItems.reduce((r, i) => r + i.size, 0);
            let emptyDelta = this.size - contentSize;
            const indexes = range(this.viewItems.length - 1, -1);
            const lowPriorityIndexes = indexes.filter((i) => this.viewItems[i].priority === exports.LayoutPriority.Low);
            const highPriorityIndexes = indexes.filter((i) => this.viewItems[i].priority === exports.LayoutPriority.High);
            for (const index of highPriorityIndexes) {
                pushToStart(indexes, index);
            }
            for (const index of lowPriorityIndexes) {
                pushToEnd(indexes, index);
            }
            if (typeof lowPriorityIndex === 'number') {
                pushToEnd(indexes, lowPriorityIndex);
            }
            for (let i = 0; emptyDelta !== 0 && i < indexes.length; i++) {
                const item = this.viewItems[indexes[i]];
                const size = clamp(item.size + emptyDelta, item.minimumSize, item.maximumSize);
                const viewDelta = size - item.size;
                emptyDelta -= viewDelta;
                item.size = size;
            }
        }
        saveProportions() {
            if (this.proportionalLayout && this._contentSize > 0) {
                this._proportions = this.viewItems.map((i) => i.visible ? i.size / this._contentSize : undefined);
            }
        }
        /**
         * Margin explain:
         *
         * For `n` views in a splitview there will be `n-1` margins `m`.
         *
         * To fit the margins each view must reduce in size by `(m * (n - 1)) / n`.
         *
         * For each view `i` the offet must be adjusted by `m * i/(n - 1)`.
         */
        layoutViews() {
            this._contentSize = this.viewItems.reduce((r, i) => r + i.size, 0);
            this.updateSashEnablement();
            if (this.viewItems.length === 0) {
                return;
            }
            const visibleViewItems = this.viewItems.filter((i) => i.visible);
            const sashCount = Math.max(0, visibleViewItems.length - 1);
            const marginReducedSize = (this.margin * sashCount) / Math.max(1, visibleViewItems.length);
            let totalLeftOffset = 0;
            const viewLeftOffsets = [];
            const sashWidth = 4; // hardcoded in css
            const runningVisiblePanelCount = this.viewItems.reduce((arr, viewItem, i) => {
                const flag = viewItem.visible ? 1 : 0;
                if (i === 0) {
                    arr.push(flag);
                }
                else {
                    arr.push(arr[i - 1] + flag);
                }
                return arr;
            }, []);
            // calculate both view and cash positions
            this.viewItems.forEach((view, i) => {
                totalLeftOffset += this.viewItems[i].size;
                viewLeftOffsets.push(totalLeftOffset);
                const size = view.visible ? view.size - marginReducedSize : 0;
                const visiblePanelsBeforeThisView = Math.max(0, runningVisiblePanelCount[i] - 1);
                const offset = i === 0 || visiblePanelsBeforeThisView === 0
                    ? 0
                    : viewLeftOffsets[i - 1] +
                        (visiblePanelsBeforeThisView / sashCount) *
                            marginReducedSize;
                if (i < this.viewItems.length - 1) {
                    // calculate sash position
                    const newSize = view.visible
                        ? offset + size - sashWidth / 2 + this.margin / 2
                        : offset;
                    if (this._orientation === exports.Orientation.HORIZONTAL) {
                        this.sashes[i].container.style.left = `${newSize}px`;
                        this.sashes[i].container.style.top = `0px`;
                    }
                    if (this._orientation === exports.Orientation.VERTICAL) {
                        this.sashes[i].container.style.left = `0px`;
                        this.sashes[i].container.style.top = `${newSize}px`;
                    }
                }
                // calculate view position
                if (this._orientation === exports.Orientation.HORIZONTAL) {
                    view.container.style.width = `${size}px`;
                    view.container.style.left = `${offset}px`;
                    view.container.style.top = '';
                    view.container.style.height = '';
                }
                if (this._orientation === exports.Orientation.VERTICAL) {
                    view.container.style.height = `${size}px`;
                    view.container.style.top = `${offset}px`;
                    view.container.style.width = '';
                    view.container.style.left = '';
                }
                view.view.layout(view.size - marginReducedSize, this._orthogonalSize);
            });
        }
        findFirstSnapIndex(indexes) {
            // visible views first
            for (const index of indexes) {
                const viewItem = this.viewItems[index];
                if (!viewItem.visible) {
                    continue;
                }
                if (viewItem.snap) {
                    return index;
                }
            }
            // then, hidden views
            for (const index of indexes) {
                const viewItem = this.viewItems[index];
                if (viewItem.visible &&
                    viewItem.maximumSize - viewItem.minimumSize > 0) {
                    return undefined;
                }
                if (!viewItem.visible && viewItem.snap) {
                    return index;
                }
            }
            return undefined;
        }
        updateSashEnablement() {
            let previous = false;
            const collapsesDown = this.viewItems.map((i) => (previous = i.size - i.minimumSize > 0 || previous));
            previous = false;
            const expandsDown = this.viewItems.map((i) => (previous = i.maximumSize - i.size > 0 || previous));
            const reverseViews = [...this.viewItems].reverse();
            previous = false;
            const collapsesUp = reverseViews
                .map((i) => (previous = i.size - i.minimumSize > 0 || previous))
                .reverse();
            previous = false;
            const expandsUp = reverseViews
                .map((i) => (previous = i.maximumSize - i.size > 0 || previous))
                .reverse();
            let position = 0;
            for (let index = 0; index < this.sashes.length; index++) {
                const sash = this.sashes[index];
                const viewItem = this.viewItems[index];
                position += viewItem.size;
                const min = !(collapsesDown[index] && expandsUp[index + 1]);
                const max = !(expandsDown[index] && collapsesUp[index + 1]);
                if (min && max) {
                    const upIndexes = range(index, -1);
                    const downIndexes = range(index + 1, this.viewItems.length);
                    const snapBeforeIndex = this.findFirstSnapIndex(upIndexes);
                    const snapAfterIndex = this.findFirstSnapIndex(downIndexes);
                    const snappedBefore = typeof snapBeforeIndex === 'number' &&
                        !this.viewItems[snapBeforeIndex].visible;
                    const snappedAfter = typeof snapAfterIndex === 'number' &&
                        !this.viewItems[snapAfterIndex].visible;
                    if (snappedBefore &&
                        collapsesUp[index] &&
                        (position > 0 || this.startSnappingEnabled)) {
                        this.updateSash(sash, exports.SashState.MINIMUM);
                    }
                    else if (snappedAfter &&
                        collapsesDown[index] &&
                        (position < this._contentSize || this.endSnappingEnabled)) {
                        this.updateSash(sash, exports.SashState.MAXIMUM);
                    }
                    else {
                        this.updateSash(sash, exports.SashState.DISABLED);
                    }
                }
                else if (min && !max) {
                    this.updateSash(sash, exports.SashState.MINIMUM);
                }
                else if (!min && max) {
                    this.updateSash(sash, exports.SashState.MAXIMUM);
                }
                else {
                    this.updateSash(sash, exports.SashState.ENABLED);
                }
            }
        }
        updateSash(sash, state) {
            toggleClass(sash.container, 'dv-disabled', state === exports.SashState.DISABLED);
            toggleClass(sash.container, 'dv-enabled', state === exports.SashState.ENABLED);
            toggleClass(sash.container, 'dv-maximum', state === exports.SashState.MAXIMUM);
            toggleClass(sash.container, 'dv-minimum', state === exports.SashState.MINIMUM);
        }
        createViewContainer() {
            const element = document.createElement('div');
            element.className = 'dv-view-container';
            return element;
        }
        createSashContainer() {
            const element = document.createElement('div');
            element.className = 'dv-sash-container';
            return element;
        }
        createContainer() {
            const element = document.createElement('div');
            const orientationClassname = this._orientation === exports.Orientation.HORIZONTAL
                ? 'dv-horizontal'
                : 'dv-vertical';
            element.className = `dv-split-view-container ${orientationClassname}`;
            return element;
        }
        dispose() {
            this._onDidSashEnd.dispose();
            this._onDidAddView.dispose();
            this._onDidRemoveView.dispose();
            for (let i = 0; i < this.element.children.length; i++) {
                if (this.element.children.item(i) === this.element) {
                    this.element.removeChild(this.element);
                    break;
                }
            }
            for (const viewItem of this.viewItems) {
                viewItem.dispose();
            }
            this.element.remove();
        }
    }

    const PROPERTY_KEYS_SPLITVIEW = (() => {
        /**
         * by readong the keys from an empty value object TypeScript will error
         * when we add or remove new properties to `DockviewOptions`
         */
        const properties = {
            orientation: undefined,
            descriptor: undefined,
            proportionalLayout: undefined,
            styles: undefined,
            margin: undefined,
            disableAutoResizing: undefined,
            className: undefined,
        };
        return Object.keys(properties);
    })();

    class Paneview extends CompositeDisposable {
        get onDidAddView() {
            return this.splitview.onDidAddView;
        }
        get onDidRemoveView() {
            return this.splitview.onDidRemoveView;
        }
        get minimumSize() {
            return this.splitview.minimumSize;
        }
        get maximumSize() {
            return this.splitview.maximumSize;
        }
        get orientation() {
            return this.splitview.orientation;
        }
        get size() {
            return this.splitview.size;
        }
        get orthogonalSize() {
            return this.splitview.orthogonalSize;
        }
        constructor(container, options) {
            var _a;
            super();
            this.paneItems = [];
            this.skipAnimation = false;
            this._onDidChange = new Emitter();
            this.onDidChange = this._onDidChange.event;
            this._orientation = (_a = options.orientation) !== null && _a !== void 0 ? _a : exports.Orientation.VERTICAL;
            this.element = document.createElement('div');
            this.element.className = 'dv-pane-container';
            container.appendChild(this.element);
            this.splitview = new Splitview(this.element, {
                orientation: this._orientation,
                proportionalLayout: false,
                descriptor: options.descriptor,
            });
            // if we've added views from the descriptor we need to
            // add the panes to our Pane array and setup animation
            this.getPanes().forEach((pane) => {
                const disposable = new CompositeDisposable(pane.onDidChangeExpansionState(() => {
                    this.setupAnimation();
                    this._onDidChange.fire(undefined);
                }));
                const paneItem = {
                    pane,
                    disposable: {
                        dispose: () => {
                            disposable.dispose();
                        },
                    },
                };
                this.paneItems.push(paneItem);
                pane.orthogonalSize = this.splitview.orthogonalSize;
            });
            this.addDisposables(this._onDidChange, this.splitview.onDidSashEnd(() => {
                this._onDidChange.fire(undefined);
            }), this.splitview.onDidAddView(() => {
                this._onDidChange.fire();
            }), this.splitview.onDidRemoveView(() => {
                this._onDidChange.fire();
            }));
        }
        setViewVisible(index, visible) {
            this.splitview.setViewVisible(index, visible);
        }
        addPane(pane, size, index = this.splitview.length, skipLayout = false) {
            const disposable = pane.onDidChangeExpansionState(() => {
                this.setupAnimation();
                this._onDidChange.fire(undefined);
            });
            const paneItem = {
                pane,
                disposable: {
                    dispose: () => {
                        disposable.dispose();
                    },
                },
            };
            this.paneItems.splice(index, 0, paneItem);
            pane.orthogonalSize = this.splitview.orthogonalSize;
            this.splitview.addView(pane, size, index, skipLayout);
        }
        getViewSize(index) {
            return this.splitview.getViewSize(index);
        }
        getPanes() {
            return this.splitview.getViews();
        }
        removePane(index, options = { skipDispose: false }) {
            const paneItem = this.paneItems.splice(index, 1)[0];
            this.splitview.removeView(index);
            if (!options.skipDispose) {
                paneItem.disposable.dispose();
                paneItem.pane.dispose();
            }
            return paneItem;
        }
        moveView(from, to) {
            if (from === to) {
                return;
            }
            const view = this.removePane(from, { skipDispose: true });
            this.skipAnimation = true;
            try {
                this.addPane(view.pane, view.pane.size, to, false);
            }
            finally {
                this.skipAnimation = false;
            }
        }
        layout(size, orthogonalSize) {
            this.splitview.layout(size, orthogonalSize);
        }
        setupAnimation() {
            if (this.skipAnimation) {
                return;
            }
            if (this.animationTimer) {
                clearTimeout(this.animationTimer);
                this.animationTimer = undefined;
            }
            addClasses(this.element, 'dv-animated');
            this.animationTimer = setTimeout(() => {
                this.animationTimer = undefined;
                removeClasses(this.element, 'dv-animated');
            }, 200);
        }
        dispose() {
            super.dispose();
            if (this.animationTimer) {
                clearTimeout(this.animationTimer);
                this.animationTimer = undefined;
            }
            this.paneItems.forEach((paneItem) => {
                paneItem.disposable.dispose();
                paneItem.pane.dispose();
            });
            this.paneItems = [];
            this.splitview.dispose();
            this.element.remove();
        }
    }

    /*---------------------------------------------------------------------------------------------
     * Accreditation: This file is largly based upon the MIT licenced VSCode sourcecode found at:
     * https://github.com/microsoft/vscode/tree/main/src/vs/base/browser/ui/grid
     *--------------------------------------------------------------------------------------------*/
    class LeafNode {
        get minimumWidth() {
            return this.view.minimumWidth;
        }
        get maximumWidth() {
            return this.view.maximumWidth;
        }
        get minimumHeight() {
            return this.view.minimumHeight;
        }
        get maximumHeight() {
            return this.view.maximumHeight;
        }
        get priority() {
            return this.view.priority;
        }
        get snap() {
            return this.view.snap;
        }
        get minimumSize() {
            return this.orientation === exports.Orientation.HORIZONTAL
                ? this.minimumHeight
                : this.minimumWidth;
        }
        get maximumSize() {
            return this.orientation === exports.Orientation.HORIZONTAL
                ? this.maximumHeight
                : this.maximumWidth;
        }
        get minimumOrthogonalSize() {
            return this.orientation === exports.Orientation.HORIZONTAL
                ? this.minimumWidth
                : this.minimumHeight;
        }
        get maximumOrthogonalSize() {
            return this.orientation === exports.Orientation.HORIZONTAL
                ? this.maximumWidth
                : this.maximumHeight;
        }
        get orthogonalSize() {
            return this._orthogonalSize;
        }
        get size() {
            return this._size;
        }
        get element() {
            return this.view.element;
        }
        get width() {
            return this.orientation === exports.Orientation.HORIZONTAL
                ? this.orthogonalSize
                : this.size;
        }
        get height() {
            return this.orientation === exports.Orientation.HORIZONTAL
                ? this.size
                : this.orthogonalSize;
        }
        constructor(view, orientation, orthogonalSize, size = 0) {
            this.view = view;
            this.orientation = orientation;
            this._onDidChange = new Emitter();
            this.onDidChange = this._onDidChange.event;
            this._orthogonalSize = orthogonalSize;
            this._size = size;
            this._disposable = this.view.onDidChange((event) => {
                if (event) {
                    this._onDidChange.fire({
                        size: this.orientation === exports.Orientation.VERTICAL
                            ? event.width
                            : event.height,
                        orthogonalSize: this.orientation === exports.Orientation.VERTICAL
                            ? event.height
                            : event.width,
                    });
                }
                else {
                    this._onDidChange.fire({});
                }
            });
        }
        setVisible(visible) {
            if (this.view.setVisible) {
                this.view.setVisible(visible);
            }
        }
        layout(size, orthogonalSize) {
            this._size = size;
            this._orthogonalSize = orthogonalSize;
            this.view.layout(this.width, this.height);
        }
        dispose() {
            this._onDidChange.dispose();
            this._disposable.dispose();
        }
    }

    /*---------------------------------------------------------------------------------------------
     * Accreditation: This file is largly based upon the MIT licenced VSCode sourcecode found at:
     * https://github.com/microsoft/vscode/tree/main/src/vs/base/browser/ui/grid
     *--------------------------------------------------------------------------------------------*/
    class BranchNode extends CompositeDisposable {
        get width() {
            return this.orientation === exports.Orientation.HORIZONTAL
                ? this.size
                : this.orthogonalSize;
        }
        get height() {
            return this.orientation === exports.Orientation.HORIZONTAL
                ? this.orthogonalSize
                : this.size;
        }
        get minimumSize() {
            return this.children.length === 0
                ? 0
                : Math.max(...this.children.map((c, index) => this.splitview.isViewVisible(index)
                    ? c.minimumOrthogonalSize
                    : 0));
        }
        get maximumSize() {
            return Math.min(...this.children.map((c, index) => this.splitview.isViewVisible(index)
                ? c.maximumOrthogonalSize
                : Number.POSITIVE_INFINITY));
        }
        get minimumOrthogonalSize() {
            return this.splitview.minimumSize;
        }
        get maximumOrthogonalSize() {
            return this.splitview.maximumSize;
        }
        get orthogonalSize() {
            return this._orthogonalSize;
        }
        get size() {
            return this._size;
        }
        get minimumWidth() {
            return this.orientation === exports.Orientation.HORIZONTAL
                ? this.minimumOrthogonalSize
                : this.minimumSize;
        }
        get minimumHeight() {
            return this.orientation === exports.Orientation.HORIZONTAL
                ? this.minimumSize
                : this.minimumOrthogonalSize;
        }
        get maximumWidth() {
            return this.orientation === exports.Orientation.HORIZONTAL
                ? this.maximumOrthogonalSize
                : this.maximumSize;
        }
        get maximumHeight() {
            return this.orientation === exports.Orientation.HORIZONTAL
                ? this.maximumSize
                : this.maximumOrthogonalSize;
        }
        get priority() {
            if (this.children.length === 0) {
                return exports.LayoutPriority.Normal;
            }
            const priorities = this.children.map((c) => typeof c.priority === 'undefined'
                ? exports.LayoutPriority.Normal
                : c.priority);
            if (priorities.some((p) => p === exports.LayoutPriority.High)) {
                return exports.LayoutPriority.High;
            }
            else if (priorities.some((p) => p === exports.LayoutPriority.Low)) {
                return exports.LayoutPriority.Low;
            }
            return exports.LayoutPriority.Normal;
        }
        get disabled() {
            return this.splitview.disabled;
        }
        set disabled(value) {
            this.splitview.disabled = value;
        }
        get margin() {
            return this.splitview.margin;
        }
        set margin(value) {
            this.splitview.margin = value;
            this.children.forEach((child) => {
                if (child instanceof BranchNode) {
                    child.margin = value;
                }
            });
        }
        constructor(orientation, proportionalLayout, styles, size, orthogonalSize, disabled, margin, childDescriptors) {
            super();
            this.orientation = orientation;
            this.proportionalLayout = proportionalLayout;
            this.styles = styles;
            this._childrenDisposable = exports.DockviewDisposable.NONE;
            this.children = [];
            this._onDidChange = new Emitter();
            this.onDidChange = this._onDidChange.event;
            this._onDidVisibilityChange = new Emitter();
            this.onDidVisibilityChange = this._onDidVisibilityChange.event;
            this._orthogonalSize = orthogonalSize;
            this._size = size;
            this.element = document.createElement('div');
            this.element.className = 'dv-branch-node';
            if (!childDescriptors) {
                this.splitview = new Splitview(this.element, {
                    orientation: this.orientation,
                    proportionalLayout,
                    styles,
                    margin,
                });
                this.splitview.layout(this.size, this.orthogonalSize);
            }
            else {
                const descriptor = {
                    views: childDescriptors.map((childDescriptor) => {
                        return {
                            view: childDescriptor.node,
                            size: childDescriptor.node.size,
                            visible: childDescriptor.node instanceof LeafNode &&
                                childDescriptor.visible !== undefined
                                ? childDescriptor.visible
                                : true,
                        };
                    }),
                    size: this.orthogonalSize,
                };
                this.children = childDescriptors.map((c) => c.node);
                this.splitview = new Splitview(this.element, {
                    orientation: this.orientation,
                    descriptor,
                    proportionalLayout,
                    styles,
                    margin,
                });
            }
            this.disabled = disabled;
            this.addDisposables(this._onDidChange, this._onDidVisibilityChange, this.splitview.onDidSashEnd(() => {
                this._onDidChange.fire({});
            }));
            this.setupChildrenEvents();
        }
        setVisible(_visible) {
            // noop
        }
        isChildVisible(index) {
            if (index < 0 || index >= this.children.length) {
                throw new Error('Invalid index');
            }
            return this.splitview.isViewVisible(index);
        }
        setChildVisible(index, visible) {
            if (index < 0 || index >= this.children.length) {
                throw new Error('Invalid index');
            }
            if (this.splitview.isViewVisible(index) === visible) {
                return;
            }
            const wereAllChildrenHidden = this.splitview.contentSize === 0;
            this.splitview.setViewVisible(index, visible);
            // }
            const areAllChildrenHidden = this.splitview.contentSize === 0;
            // If all children are hidden then the parent should hide the entire splitview
            // If the entire splitview is hidden then the parent should show the splitview when a child is shown
            if ((visible && wereAllChildrenHidden) ||
                (!visible && areAllChildrenHidden)) {
                this._onDidVisibilityChange.fire({ visible });
            }
        }
        moveChild(from, to) {
            if (from === to) {
                return;
            }
            if (from < 0 || from >= this.children.length) {
                throw new Error('Invalid from index');
            }
            if (from < to) {
                to--;
            }
            this.splitview.moveView(from, to);
            const child = this._removeChild(from);
            this._addChild(child, to);
        }
        getChildSize(index) {
            if (index < 0 || index >= this.children.length) {
                throw new Error('Invalid index');
            }
            return this.splitview.getViewSize(index);
        }
        resizeChild(index, size) {
            if (index < 0 || index >= this.children.length) {
                throw new Error('Invalid index');
            }
            this.splitview.resizeView(index, size);
        }
        layout(size, orthogonalSize) {
            this._size = orthogonalSize;
            this._orthogonalSize = size;
            this.splitview.layout(orthogonalSize, size);
        }
        addChild(node, size, index, skipLayout) {
            if (index < 0 || index > this.children.length) {
                throw new Error('Invalid index');
            }
            this.splitview.addView(node, size, index, skipLayout);
            this._addChild(node, index);
        }
        getChildCachedVisibleSize(index) {
            if (index < 0 || index >= this.children.length) {
                throw new Error('Invalid index');
            }
            return this.splitview.getViewCachedVisibleSize(index);
        }
        removeChild(index, sizing) {
            if (index < 0 || index >= this.children.length) {
                throw new Error('Invalid index');
            }
            this.splitview.removeView(index, sizing);
            return this._removeChild(index);
        }
        _addChild(node, index) {
            this.children.splice(index, 0, node);
            this.setupChildrenEvents();
        }
        _removeChild(index) {
            const [child] = this.children.splice(index, 1);
            this.setupChildrenEvents();
            return child;
        }
        setupChildrenEvents() {
            this._childrenDisposable.dispose();
            this._childrenDisposable = new CompositeDisposable(exports.DockviewEvent.any(...this.children.map((c) => c.onDidChange))((e) => {
                /**
                 * indicate a change has occured to allows any re-rendering but don't bubble
                 * event because that was specific to this branch
                 */
                this._onDidChange.fire({ size: e.orthogonalSize });
            }), ...this.children.map((c, i) => {
                if (c instanceof BranchNode) {
                    return c.onDidVisibilityChange(({ visible }) => {
                        this.setChildVisible(i, visible);
                    });
                }
                return exports.DockviewDisposable.NONE;
            }));
        }
        dispose() {
            this._childrenDisposable.dispose();
            this.splitview.dispose();
            this.children.forEach((child) => child.dispose());
            super.dispose();
        }
    }

    /*---------------------------------------------------------------------------------------------
     * Accreditation: This file is largly based upon the MIT licenced VSCode sourcecode found at:
     * https://github.com/microsoft/vscode/tree/main/src/vs/base/browser/ui/grid
     *--------------------------------------------------------------------------------------------*/
    function findLeaf(candiateNode, last) {
        if (candiateNode instanceof LeafNode) {
            return candiateNode;
        }
        if (candiateNode instanceof BranchNode) {
            return findLeaf(candiateNode.children[last ? candiateNode.children.length - 1 : 0], last);
        }
        throw new Error('invalid node');
    }
    function cloneNode(node, size, orthogonalSize) {
        if (node instanceof BranchNode) {
            const result = new BranchNode(node.orientation, node.proportionalLayout, node.styles, size, orthogonalSize, node.disabled, node.margin);
            for (let i = node.children.length - 1; i >= 0; i--) {
                const child = node.children[i];
                result.addChild(cloneNode(child, child.size, child.orthogonalSize), child.size, 0, true);
            }
            return result;
        }
        else {
            return new LeafNode(node.view, node.orientation, orthogonalSize);
        }
    }
    function flipNode(node, size, orthogonalSize) {
        if (node instanceof BranchNode) {
            const result = new BranchNode(orthogonal(node.orientation), node.proportionalLayout, node.styles, size, orthogonalSize, node.disabled, node.margin);
            let totalSize = 0;
            for (let i = node.children.length - 1; i >= 0; i--) {
                const child = node.children[i];
                const childSize = child instanceof BranchNode ? child.orthogonalSize : child.size;
                let newSize = node.size === 0
                    ? 0
                    : Math.round((size * childSize) / node.size);
                totalSize += newSize;
                // The last view to add should adjust to rounding errors
                if (i === 0) {
                    newSize += size - totalSize;
                }
                result.addChild(flipNode(child, orthogonalSize, newSize), newSize, 0, true);
            }
            return result;
        }
        else {
            return new LeafNode(node.view, orthogonal(node.orientation), orthogonalSize);
        }
    }
    function indexInParent(element) {
        const parentElement = element.parentElement;
        if (!parentElement) {
            throw new Error('Invalid grid element');
        }
        let el = parentElement.firstElementChild;
        let index = 0;
        while (el !== element && el !== parentElement.lastElementChild && el) {
            el = el.nextElementSibling;
            index++;
        }
        return index;
    }
    /**
     * Find the grid location of a specific DOM element by traversing the parent
     * chain and finding each child index on the way.
     *
     * This will break as soon as DOM structures of the Splitview or Gridview change.
     */
    function getGridLocation(element) {
        const parentElement = element.parentElement;
        if (!parentElement) {
            throw new Error('Invalid grid element');
        }
        if (/\bdv-grid-view\b/.test(parentElement.className)) {
            return [];
        }
        const index = indexInParent(parentElement);
        const ancestor = parentElement.parentElement.parentElement.parentElement;
        return [...getGridLocation(ancestor), index];
    }
    function getRelativeLocation(rootOrientation, location, direction) {
        const orientation = getLocationOrientation(rootOrientation, location);
        const directionOrientation = getDirectionOrientation(direction);
        if (orientation === directionOrientation) {
            const [rest, _index] = tail(location);
            let index = _index;
            if (direction === 'right' || direction === 'bottom') {
                index += 1;
            }
            return [...rest, index];
        }
        else {
            const index = direction === 'right' || direction === 'bottom' ? 1 : 0;
            return [...location, index];
        }
    }
    function getDirectionOrientation(direction) {
        return direction === 'top' || direction === 'bottom'
            ? exports.Orientation.VERTICAL
            : exports.Orientation.HORIZONTAL;
    }
    function getLocationOrientation(rootOrientation, location) {
        return location.length % 2 === 0
            ? orthogonal(rootOrientation)
            : rootOrientation;
    }
    const orthogonal = (orientation) => orientation === exports.Orientation.HORIZONTAL
        ? exports.Orientation.VERTICAL
        : exports.Orientation.HORIZONTAL;
    function isGridBranchNode(node) {
        return !!node.children;
    }
    const serializeBranchNode = (node, orientation) => {
        const size = orientation === exports.Orientation.VERTICAL ? node.box.width : node.box.height;
        if (!isGridBranchNode(node)) {
            if (typeof node.cachedVisibleSize === 'number') {
                return {
                    type: 'leaf',
                    data: node.view.toJSON(),
                    size: node.cachedVisibleSize,
                    visible: false,
                };
            }
            return { type: 'leaf', data: node.view.toJSON(), size };
        }
        return {
            type: 'branch',
            data: node.children.map((c) => serializeBranchNode(c, orthogonal(orientation))),
            size,
        };
    };
    class Gridview {
        get length() {
            return this._root ? this._root.children.length : 0;
        }
        get orientation() {
            return this.root.orientation;
        }
        set orientation(orientation) {
            if (this.root.orientation === orientation) {
                return;
            }
            const { size, orthogonalSize } = this.root;
            this.root = flipNode(this.root, orthogonalSize, size);
            this.root.layout(size, orthogonalSize);
        }
        get width() {
            return this.root.width;
        }
        get height() {
            return this.root.height;
        }
        get minimumWidth() {
            return this.root.minimumWidth;
        }
        get minimumHeight() {
            return this.root.minimumHeight;
        }
        get maximumWidth() {
            return this.root.maximumHeight;
        }
        get maximumHeight() {
            return this.root.maximumHeight;
        }
        get locked() {
            return this._locked;
        }
        set locked(value) {
            this._locked = value;
            const branch = [this.root];
            /**
             * simple depth-first-search to cover all nodes
             *
             * @see https://en.wikipedia.org/wiki/Depth-first_search
             */
            while (branch.length > 0) {
                const node = branch.pop();
                if (node instanceof BranchNode) {
                    node.disabled = value;
                    branch.push(...node.children);
                }
            }
        }
        get margin() {
            return this._margin;
        }
        set margin(value) {
            this._margin = value;
            this.root.margin = value;
        }
        maximizedView() {
            var _a;
            return (_a = this._maximizedNode) === null || _a === void 0 ? void 0 : _a.leaf.view;
        }
        hasMaximizedView() {
            return this._maximizedNode !== undefined;
        }
        maximizeView(view) {
            var _a;
            const location = getGridLocation(view.element);
            const [_, node] = this.getNode(location);
            if (!(node instanceof LeafNode)) {
                return;
            }
            if (((_a = this._maximizedNode) === null || _a === void 0 ? void 0 : _a.leaf) === node) {
                return;
            }
            if (this.hasMaximizedView()) {
                this.exitMaximizedView();
            }
            serializeBranchNode(this.getView(), this.orientation);
            const hiddenOnMaximize = [];
            function hideAllViewsBut(parent, exclude) {
                for (let i = 0; i < parent.children.length; i++) {
                    const child = parent.children[i];
                    if (child instanceof LeafNode) {
                        if (child !== exclude) {
                            if (parent.isChildVisible(i)) {
                                parent.setChildVisible(i, false);
                            }
                            else {
                                hiddenOnMaximize.push(child);
                            }
                        }
                    }
                    else {
                        hideAllViewsBut(child, exclude);
                    }
                }
            }
            hideAllViewsBut(this.root, node);
            this._maximizedNode = { leaf: node, hiddenOnMaximize };
            this._onDidMaximizedNodeChange.fire({
                view: node.view,
                isMaximized: true,
            });
        }
        exitMaximizedView() {
            if (!this._maximizedNode) {
                return;
            }
            const hiddenOnMaximize = this._maximizedNode.hiddenOnMaximize;
            function showViewsInReverseOrder(parent) {
                for (let index = parent.children.length - 1; index >= 0; index--) {
                    const child = parent.children[index];
                    if (child instanceof LeafNode) {
                        if (!hiddenOnMaximize.includes(child)) {
                            parent.setChildVisible(index, true);
                        }
                    }
                    else {
                        showViewsInReverseOrder(child);
                    }
                }
            }
            showViewsInReverseOrder(this.root);
            const tmp = this._maximizedNode.leaf;
            this._maximizedNode = undefined;
            this._onDidMaximizedNodeChange.fire({
                view: tmp.view,
                isMaximized: false,
            });
        }
        serialize() {
            const maximizedView = this.maximizedView();
            let maxmizedViewLocation;
            if (maximizedView) {
                /**
                 * The minimum information we can get away with in order to serialize a maxmized view is it's location within the grid
                 * which is represented as a branch of indices
                 */
                maxmizedViewLocation = getGridLocation(maximizedView.element);
            }
            /**
             * We pause the onDidMaximizedNodeChange events because this method needs to
             * call `this.exitMaximizedView()`. We don't want this to invoke any listeners
             * since we undo it before leaving this method
             */
            const pauseToken = this._onDidMaximizedNodeChange.pause();
            try {
                if (this.hasMaximizedView()) {
                    /**
                     * the saved layout cannot be in its maxmized state otherwise all of the underlying
                     * view dimensions will be wrong
                     *
                     * To counteract this we temporaily remove the maximized view to compute the serialized output
                     * of the grid before adding back the maxmized view as to not alter the layout from the users
                     * perspective when `.toJSON()` is called
                     */
                    this.exitMaximizedView();
                }
                const root = serializeBranchNode(this.getView(), this.orientation);
                const result = {
                    root,
                    width: this.width,
                    height: this.height,
                    orientation: this.orientation,
                };
                if (maxmizedViewLocation) {
                    result.maximizedNode = {
                        location: maxmizedViewLocation,
                    };
                }
                if (maximizedView) {
                    // replace any maximzied view that was removed for serialization purposes
                    this.maximizeView(maximizedView);
                }
                return result;
            }
            finally {
                pauseToken.dispose();
            }
        }
        dispose() {
            this.disposable.dispose();
            this._onDidChange.dispose();
            this._onDidMaximizedNodeChange.dispose();
            this._onDidViewVisibilityChange.dispose();
            this.root.dispose();
            this._maximizedNode = undefined;
            this.element.remove();
        }
        clear() {
            const orientation = this.root.orientation;
            this.root = new BranchNode(orientation, this.proportionalLayout, this.styles, this.root.size, this.root.orthogonalSize, this.locked, this.margin);
        }
        deserialize(json, deserializer) {
            const orientation = json.orientation;
            const height = orientation === exports.Orientation.VERTICAL ? json.height : json.width;
            this._deserialize(json.root, orientation, deserializer, height);
            /**
             * The deserialied layout must be positioned through this.layout(...)
             * before any maximizedNode can be positioned
             */
            this.layout(json.width, json.height);
            if (json.maximizedNode) {
                const location = json.maximizedNode.location;
                const [_, node] = this.getNode(location);
                if (!(node instanceof LeafNode)) {
                    return;
                }
                this.maximizeView(node.view);
            }
        }
        _deserialize(root, orientation, deserializer, orthogonalSize) {
            this.root = this._deserializeNode(root, orientation, deserializer, orthogonalSize);
        }
        _deserializeNode(node, orientation, deserializer, orthogonalSize) {
            var _a;
            let result;
            if (node.type === 'branch') {
                const serializedChildren = node.data;
                const children = serializedChildren.map((serializedChild) => {
                    return {
                        node: this._deserializeNode(serializedChild, orthogonal(orientation), deserializer, node.size),
                        visible: serializedChild.visible,
                    };
                });
                result = new BranchNode(orientation, this.proportionalLayout, this.styles, node.size, // <- orthogonal size - flips at each depth
                orthogonalSize, // <- size - flips at each depth,
                this.locked, this.margin, children);
            }
            else {
                const view = deserializer.fromJSON(node);
                if (typeof node.visible === 'boolean') {
                    (_a = view.setVisible) === null || _a === void 0 ? void 0 : _a.call(view, node.visible);
                }
                result = new LeafNode(view, orientation, orthogonalSize, node.size);
            }
            return result;
        }
        get root() {
            return this._root;
        }
        set root(root) {
            const oldRoot = this._root;
            if (oldRoot) {
                oldRoot.dispose();
                this._maximizedNode = undefined;
                this.element.removeChild(oldRoot.element);
            }
            this._root = root;
            this.element.appendChild(this._root.element);
            this.disposable.value = this._root.onDidChange((e) => {
                this._onDidChange.fire(e);
            });
        }
        normalize() {
            if (!this._root) {
                return;
            }
            if (this._root.children.length !== 1) {
                return;
            }
            const oldRoot = this.root;
            // can remove one level of redundant branching if there is only a single child
            const childReference = oldRoot.children[0];
            if (childReference instanceof LeafNode) {
                return;
            }
            oldRoot.element.remove();
            const child = oldRoot.removeChild(0); // Remove child to prevent double disposal
            oldRoot.dispose(); // Dispose old root (won't dispose removed child)
            child.dispose(); // Dispose the removed child
            this._root = cloneNode(childReference, childReference.size, childReference.orthogonalSize);
            this.element.appendChild(this._root.element);
            this.disposable.value = this._root.onDidChange((e) => {
                this._onDidChange.fire(e);
            });
        }
        /**
         * If the root is orientated as a VERTICAL node then nest the existing root within a new HORIZIONTAL root node
         * If the root is orientated as a HORIZONTAL node then nest the existing root within a new VERITCAL root node
         */
        insertOrthogonalSplitviewAtRoot() {
            if (!this._root) {
                return;
            }
            const oldRoot = this.root;
            oldRoot.element.remove();
            this._root = new BranchNode(orthogonal(oldRoot.orientation), this.proportionalLayout, this.styles, this.root.orthogonalSize, this.root.size, this.locked, this.margin);
            if (oldRoot.children.length === 0) ;
            else if (oldRoot.children.length === 1) {
                // can remove one level of redundant branching if there is only a single child
                const childReference = oldRoot.children[0];
                const child = oldRoot.removeChild(0); // remove to prevent disposal when disposing of unwanted root
                child.dispose();
                oldRoot.dispose();
                this._root.addChild(
                /**
                 * the child node will have the same orientation as the new root since
                 * we are removing the inbetween node.
                 * the entire 'tree' must be flipped recursively to ensure that the orientation
                 * flips at each level
                 */
                flipNode(childReference, childReference.orthogonalSize, childReference.size), exports.Sizing.Distribute, 0);
            }
            else {
                this._root.addChild(oldRoot, exports.Sizing.Distribute, 0);
            }
            this.element.appendChild(this._root.element);
            this.disposable.value = this._root.onDidChange((e) => {
                this._onDidChange.fire(e);
            });
        }
        next(location) {
            return this.progmaticSelect(location);
        }
        previous(location) {
            return this.progmaticSelect(location, true);
        }
        getView(location) {
            const node = location ? this.getNode(location)[1] : this.root;
            return this._getViews(node, this.orientation);
        }
        _getViews(node, orientation, cachedVisibleSize) {
            const box = { height: node.height, width: node.width };
            if (node instanceof LeafNode) {
                return { box, view: node.view, cachedVisibleSize };
            }
            const children = [];
            for (let i = 0; i < node.children.length; i++) {
                const child = node.children[i];
                const nodeCachedVisibleSize = node.getChildCachedVisibleSize(i);
                children.push(this._getViews(child, orthogonal(orientation), nodeCachedVisibleSize));
            }
            return { box, children };
        }
        progmaticSelect(location, reverse = false) {
            const [path, node] = this.getNode(location);
            if (!(node instanceof LeafNode)) {
                throw new Error('invalid location');
            }
            for (let i = path.length - 1; i > -1; i--) {
                const n = path[i];
                const l = location[i] || 0;
                const canProgressInCurrentLevel = reverse
                    ? l - 1 > -1
                    : l + 1 < n.children.length;
                if (canProgressInCurrentLevel) {
                    return findLeaf(n.children[reverse ? l - 1 : l + 1], reverse);
                }
            }
            return findLeaf(this.root, reverse);
        }
        constructor(proportionalLayout, styles, orientation, locked, margin) {
            this.proportionalLayout = proportionalLayout;
            this.styles = styles;
            this._locked = false;
            this._margin = 0;
            this._maximizedNode = undefined;
            this.disposable = new MutableDisposable();
            this._onDidChange = new Emitter();
            this.onDidChange = this._onDidChange.event;
            this._onDidViewVisibilityChange = new Emitter();
            this.onDidViewVisibilityChange = this._onDidViewVisibilityChange.event;
            this._onDidMaximizedNodeChange = new Emitter();
            this.onDidMaximizedNodeChange = this._onDidMaximizedNodeChange.event;
            this.element = document.createElement('div');
            this.element.className = 'dv-grid-view';
            this._locked = locked !== null && locked !== void 0 ? locked : false;
            this._margin = margin !== null && margin !== void 0 ? margin : 0;
            this.root = new BranchNode(orientation, proportionalLayout, styles, 0, 0, this.locked, this.margin);
        }
        isViewVisible(location) {
            const [rest, index] = tail(location);
            const [, parent] = this.getNode(rest);
            if (!(parent instanceof BranchNode)) {
                throw new Error('Invalid from location');
            }
            return parent.isChildVisible(index);
        }
        setViewVisible(location, visible) {
            if (this.hasMaximizedView()) {
                this.exitMaximizedView();
            }
            const [rest, index] = tail(location);
            const [, parent] = this.getNode(rest);
            if (!(parent instanceof BranchNode)) {
                throw new Error('Invalid from location');
            }
            this._onDidViewVisibilityChange.fire();
            parent.setChildVisible(index, visible);
        }
        moveView(parentLocation, from, to) {
            if (this.hasMaximizedView()) {
                this.exitMaximizedView();
            }
            const [, parent] = this.getNode(parentLocation);
            if (!(parent instanceof BranchNode)) {
                throw new Error('Invalid location');
            }
            parent.moveChild(from, to);
        }
        addView(view, size, location) {
            if (this.hasMaximizedView()) {
                this.exitMaximizedView();
            }
            const [rest, index] = tail(location);
            const [pathToParent, parent] = this.getNode(rest);
            if (parent instanceof BranchNode) {
                const node = new LeafNode(view, orthogonal(parent.orientation), parent.orthogonalSize);
                parent.addChild(node, size, index);
            }
            else {
                const [grandParent, ..._] = [...pathToParent].reverse();
                const [parentIndex, ...__] = [...rest].reverse();
                let newSiblingSize = 0;
                const newSiblingCachedVisibleSize = grandParent.getChildCachedVisibleSize(parentIndex);
                if (typeof newSiblingCachedVisibleSize === 'number') {
                    newSiblingSize = exports.Sizing.Invisible(newSiblingCachedVisibleSize);
                }
                const child = grandParent.removeChild(parentIndex);
                child.dispose();
                const newParent = new BranchNode(parent.orientation, this.proportionalLayout, this.styles, parent.size, parent.orthogonalSize, this.locked, this.margin);
                grandParent.addChild(newParent, parent.size, parentIndex);
                const newSibling = new LeafNode(parent.view, grandParent.orientation, parent.size);
                newParent.addChild(newSibling, newSiblingSize, 0);
                if (typeof size !== 'number' && size.type === 'split') {
                    size = { type: 'split', index: 0 };
                }
                const node = new LeafNode(view, grandParent.orientation, parent.size);
                newParent.addChild(node, size, index);
            }
        }
        remove(view, sizing) {
            const location = getGridLocation(view.element);
            return this.removeView(location, sizing);
        }
        removeView(location, sizing) {
            if (this.hasMaximizedView()) {
                this.exitMaximizedView();
            }
            const [rest, index] = tail(location);
            const [pathToParent, parent] = this.getNode(rest);
            if (!(parent instanceof BranchNode)) {
                throw new Error('Invalid location');
            }
            const nodeToRemove = parent.children[index];
            if (!(nodeToRemove instanceof LeafNode)) {
                throw new Error('Invalid location');
            }
            parent.removeChild(index, sizing);
            nodeToRemove.dispose();
            if (parent.children.length !== 1) {
                return nodeToRemove.view;
            }
            // if the parent has only one child and we know the parent is a BranchNode we can make the tree
            // more efficiently spaced by replacing the parent BranchNode with the child.
            // if that child is a LeafNode then we simply replace the BranchNode with the child otherwise if the child
            // is a BranchNode too we should spread it's children into the grandparent.
            // refer to the remaining child as the sibling
            const sibling = parent.children[0];
            if (pathToParent.length === 0) {
                // if the parent is root
                if (sibling instanceof LeafNode) {
                    // if the sibling is a leaf node no action is required
                    return nodeToRemove.view;
                }
                // otherwise the sibling is a branch node. since the parent is the root and the root has only one child
                // which is a branch node we can just set this branch node to be the new root node
                // for good housekeeping we'll removing the sibling from it's existing tree
                parent.removeChild(0, sizing);
                // and set that sibling node to be root
                this.root = sibling;
                return nodeToRemove.view;
            }
            // otherwise the parent is apart of a large sub-tree
            const [grandParent, ..._] = [...pathToParent].reverse();
            const [parentIndex, ...__] = [...rest].reverse();
            const isSiblingVisible = parent.isChildVisible(0);
            // either way we need to remove the sibling from it's existing tree
            parent.removeChild(0, sizing);
            // note the sizes of all of the grandparents children
            const sizes = grandParent.children.map((_size, i) => grandParent.getChildSize(i));
            // remove the parent from the grandparent since we are moving the sibling to take the parents place
            // this parent is no longer used and can be disposed of
            grandParent.removeChild(parentIndex, sizing).dispose();
            if (sibling instanceof BranchNode) {
                // replace the parent with the siblings children
                sizes.splice(parentIndex, 1, ...sibling.children.map((c) => c.size));
                // and add those siblings to the grandparent
                for (let i = 0; i < sibling.children.length; i++) {
                    const child = sibling.children[i];
                    grandParent.addChild(child, child.size, parentIndex + i);
                }
                /**
                 * clean down the branch node since we need to dipose of it and
                 * when .dispose() it called on a branch it will dispose of any
                 * views it is holding onto.
                 */
                while (sibling.children.length > 0) {
                    sibling.removeChild(0);
                }
            }
            else {
                // otherwise create a new leaf node and add that to the grandparent
                const newSibling = new LeafNode(sibling.view, orthogonal(sibling.orientation), sibling.size);
                const siblingSizing = isSiblingVisible
                    ? sibling.orthogonalSize
                    : exports.Sizing.Invisible(sibling.orthogonalSize);
                grandParent.addChild(newSibling, siblingSizing, parentIndex);
            }
            // the containing node of the sibling is no longer required and can be disposed of
            sibling.dispose();
            // resize everything
            for (let i = 0; i < sizes.length; i++) {
                grandParent.resizeChild(i, sizes[i]);
            }
            return nodeToRemove.view;
        }
        layout(width, height) {
            const [size, orthogonalSize] = this.root.orientation === exports.Orientation.HORIZONTAL
                ? [height, width]
                : [width, height];
            this.root.layout(size, orthogonalSize);
        }
        getNode(location, node = this.root, path = []) {
            if (location.length === 0) {
                return [path, node];
            }
            if (!(node instanceof BranchNode)) {
                throw new Error('Invalid location');
            }
            const [index, ...rest] = location;
            if (index < 0 || index >= node.children.length) {
                throw new Error('Invalid location');
            }
            const child = node.children[index];
            path.push(node);
            return this.getNode(rest, child, path);
        }
    }

    const PROPERTY_KEYS_GRIDVIEW = (() => {
        /**
         * by readong the keys from an empty value object TypeScript will error
         * when we add or remove new properties to `DockviewOptions`
         */
        const properties = {
            disableAutoResizing: undefined,
            proportionalLayout: undefined,
            orientation: undefined,
            hideBorders: undefined,
            className: undefined,
        };
        return Object.keys(properties);
    })();

    class Resizable extends CompositeDisposable {
        get element() {
            return this._element;
        }
        get disableResizing() {
            return this._disableResizing;
        }
        set disableResizing(value) {
            this._disableResizing = value;
        }
        constructor(parentElement, disableResizing = false) {
            super();
            this._lastWidth = -1;
            this._lastHeight = -1;
            this._disableResizing = disableResizing;
            this._element = parentElement;
            this.addDisposables(watchElementResize(this._element, (entry) => {
                if (this.isDisposed) {
                    /**
                     * resize is delayed through requestAnimationFrame so there is a small chance
                     * the component has already been disposed of
                     */
                    return;
                }
                if (this.disableResizing) {
                    return;
                }
                if (!this._element.offsetParent) {
                    /**
                     * offsetParent === null is equivalent to display: none being set on the element or one
                     * of it's parents. In the display: none case the size will become (0, 0) which we do
                     * not want to propagate.
                     *
                     * @see https://developer.mozilla.org/en-US/docs/Web/API/HTMLElement/offsetParent
                     *
                     * You could use checkVisibility() but at the time of writing it's not supported across
                     * all Browsers
                     *
                     * @see https://developer.mozilla.org/en-US/docs/Web/API/Element/checkVisibility
                     */
                    return;
                }
                if (!isInDocument(this._element)) {
                    /**
                     * since the event is dispatched through requestAnimationFrame there is a small chance
                     * the component is no longer attached to the DOM, if that is the case the dimensions
                     * are mostly likely all zero and meaningless. we should skip this case.
                     */
                    return;
                }
                // Round to integers to absorb sub-pixel jitter from
                // fractional devicePixelRatio (e.g. multi-monitor setups),
                // which would otherwise re-fire layout in a feedback loop.
                const width = Math.round(entry.contentRect.width);
                const height = Math.round(entry.contentRect.height);
                if (width === this._lastWidth && height === this._lastHeight) {
                    return;
                }
                this._lastWidth = width;
                this._lastHeight = height;
                this.layout(width, height);
            }));
        }
    }

    const nextLayoutId$1 = sequentialNumberGenerator();
    function toTarget(direction) {
        switch (direction) {
            case 'left':
                return 'left';
            case 'right':
                return 'right';
            case 'above':
                return 'top';
            case 'below':
                return 'bottom';
            case 'within':
            default:
                return 'center';
        }
    }
    class BaseGrid extends Resizable {
        get id() {
            return this._id;
        }
        get size() {
            return this._groups.size;
        }
        get groups() {
            return Array.from(this._groups.values()).map((_) => _.value);
        }
        get width() {
            return this.gridview.width;
        }
        get height() {
            return this.gridview.height;
        }
        get minimumHeight() {
            return this.gridview.minimumHeight;
        }
        get maximumHeight() {
            return this.gridview.maximumHeight;
        }
        get minimumWidth() {
            return this.gridview.minimumWidth;
        }
        get maximumWidth() {
            return this.gridview.maximumWidth;
        }
        get activeGroup() {
            return this._activeGroup;
        }
        get locked() {
            return this.gridview.locked;
        }
        set locked(value) {
            this.gridview.locked = value;
        }
        constructor(container, options) {
            var _a;
            super(document.createElement('div'), options.disableAutoResizing);
            this._id = nextLayoutId$1.next();
            this._groups = new Map();
            this._onDidRemove = new Emitter();
            this.onDidRemove = this._onDidRemove.event;
            this._onDidAdd = new Emitter();
            this.onDidAdd = this._onDidAdd.event;
            this._onDidMaximizedChange = new Emitter();
            this.onDidMaximizedChange = this._onDidMaximizedChange.event;
            this._onDidActiveChange = new Emitter();
            this.onDidActiveChange = this._onDidActiveChange.event;
            this._bufferOnDidLayoutChange = new AsapEvent();
            this.onDidLayoutChange = this._bufferOnDidLayoutChange.onEvent;
            this._onDidViewVisibilityChangeMicroTaskQueue = new AsapEvent();
            this.onDidViewVisibilityChangeMicroTaskQueue = this._onDidViewVisibilityChangeMicroTaskQueue.onEvent;
            this.element.style.height = '100%';
            this.element.style.width = '100%';
            this._classNames = new Classnames(this.element);
            this._classNames.setClassNames((_a = options.className) !== null && _a !== void 0 ? _a : '');
            // the container is owned by the third-party, do not modify/delete it
            container.appendChild(this.element);
            this.gridview = new Gridview(!!options.proportionalLayout, options.styles, options.orientation, options.locked, options.margin);
            this.gridview.locked = !!options.locked;
            this.element.appendChild(this.gridview.element);
            this.layout(0, 0, true); // set some elements height/widths
            this.addDisposables(this.gridview.onDidMaximizedNodeChange((event) => {
                this._onDidMaximizedChange.fire({
                    panel: event.view,
                    isMaximized: event.isMaximized,
                });
            }), this.gridview.onDidViewVisibilityChange(() => this._onDidViewVisibilityChangeMicroTaskQueue.fire()), this.onDidViewVisibilityChangeMicroTaskQueue(() => {
                this.forceRelayout();
            }), exports.DockviewDisposable.from(() => {
                var _a;
                (_a = this.element.parentElement) === null || _a === void 0 ? void 0 : _a.removeChild(this.element);
            }), this.gridview.onDidChange(() => {
                this._bufferOnDidLayoutChange.fire();
            }), exports.DockviewEvent.any(this.onDidAdd, this.onDidRemove, this.onDidActiveChange)(() => {
                this._bufferOnDidLayoutChange.fire();
            }), this._onDidMaximizedChange, this._onDidViewVisibilityChangeMicroTaskQueue, this._bufferOnDidLayoutChange);
        }
        setVisible(panel, visible) {
            this.gridview.setViewVisible(getGridLocation(panel.element), visible);
            this._bufferOnDidLayoutChange.fire();
        }
        isVisible(panel) {
            return this.gridview.isViewVisible(getGridLocation(panel.element));
        }
        updateOptions(options) {
            var _a, _b, _c, _d;
            if (typeof options.proportionalLayout === 'boolean') ;
            if (options.orientation) {
                this.gridview.orientation = options.orientation;
            }
            if ('disableResizing' in options) {
                this.disableResizing = (_a = options.disableAutoResizing) !== null && _a !== void 0 ? _a : false;
            }
            if ('locked' in options) {
                this.locked = (_b = options.locked) !== null && _b !== void 0 ? _b : false;
            }
            if ('margin' in options) {
                this.gridview.margin = (_c = options.margin) !== null && _c !== void 0 ? _c : 0;
            }
            if ('className' in options) {
                this._classNames.setClassNames((_d = options.className) !== null && _d !== void 0 ? _d : '');
            }
        }
        maximizeGroup(panel) {
            this.gridview.maximizeView(panel);
            this.doSetGroupActive(panel);
        }
        isMaximizedGroup(panel) {
            return this.gridview.maximizedView() === panel;
        }
        exitMaximizedGroup() {
            this.gridview.exitMaximizedView();
        }
        hasMaximizedGroup() {
            return this.gridview.hasMaximizedView();
        }
        doAddGroup(group, location = [0], size) {
            this.gridview.addView(group, size !== null && size !== void 0 ? size : exports.Sizing.Distribute, location);
            this._onDidAdd.fire(group);
        }
        doRemoveGroup(group, options) {
            if (!this._groups.has(group.id)) {
                throw new Error('invalid operation');
            }
            const item = this._groups.get(group.id);
            const view = this.gridview.remove(group, exports.Sizing.Distribute);
            if (item && !(options === null || options === void 0 ? void 0 : options.skipDispose)) {
                item.disposable.dispose();
                item.value.dispose();
                this._groups.delete(group.id);
                this._onDidRemove.fire(group);
            }
            if (!(options === null || options === void 0 ? void 0 : options.skipActive) && this._activeGroup === group) {
                const groups = Array.from(this._groups.values());
                this.doSetGroupActive(groups.length > 0 ? groups[0].value : undefined);
            }
            return view;
        }
        getPanel(id) {
            var _a;
            return (_a = this._groups.get(id)) === null || _a === void 0 ? void 0 : _a.value;
        }
        doSetGroupActive(group) {
            if (this._activeGroup === group) {
                return;
            }
            if (this._activeGroup) {
                this._activeGroup.setActive(false);
            }
            if (group) {
                group.setActive(true);
            }
            this._activeGroup = group;
            this._onDidActiveChange.fire(group);
        }
        removeGroup(group) {
            this.doRemoveGroup(group);
        }
        moveToNext(options) {
            var _a;
            if (!options) {
                options = {};
            }
            if (!options.group) {
                if (!this.activeGroup) {
                    return;
                }
                options.group = this.activeGroup;
            }
            const location = getGridLocation(options.group.element);
            const next = (_a = this.gridview.next(location)) === null || _a === void 0 ? void 0 : _a.view;
            this.doSetGroupActive(next);
        }
        moveToPrevious(options) {
            var _a;
            if (!options) {
                options = {};
            }
            if (!options.group) {
                if (!this.activeGroup) {
                    return;
                }
                options.group = this.activeGroup;
            }
            const location = getGridLocation(options.group.element);
            const next = (_a = this.gridview.previous(location)) === null || _a === void 0 ? void 0 : _a.view;
            this.doSetGroupActive(next);
        }
        forceRelayout() {
            this.layout(this.width, this.height, true);
        }
        layout(width, height, forceResize) {
            const different = forceResize || width !== this.width || height !== this.height;
            if (!different) {
                return;
            }
            this.gridview.element.style.height = `${height}px`;
            this.gridview.element.style.width = `${width}px`;
            this.gridview.layout(width, height);
        }
        dispose() {
            this._onDidActiveChange.dispose();
            this._onDidAdd.dispose();
            this._onDidRemove.dispose();
            for (const group of this.groups) {
                group.dispose();
            }
            this.gridview.dispose();
            super.dispose();
        }
    }

    class SplitviewApi {
        /**
         * The minimum size  the component can reach where size is measured in the direction of orientation provided.
         */
        get minimumSize() {
            return this.component.minimumSize;
        }
        /**
         * The maximum size the component can reach where size is measured in the direction of orientation provided.
         */
        get maximumSize() {
            return this.component.maximumSize;
        }
        /**
         * Width of the component.
         */
        get width() {
            return this.component.width;
        }
        /**
         * Height of the component.
         */
        get height() {
            return this.component.height;
        }
        /**
         * The current number of panels.
         */
        get length() {
            return this.component.length;
        }
        /**
         * The current orientation of the component.
         */
        get orientation() {
            return this.component.orientation;
        }
        /**
         * The list of current panels.
         */
        get panels() {
            return this.component.panels;
        }
        /**
         * Invoked after a layout is loaded through the `fromJSON` method.
         */
        get onDidLayoutFromJSON() {
            return this.component.onDidLayoutFromJSON;
        }
        /**
         * Invoked whenever any aspect of the layout changes.
         * If listening to this event it may be worth debouncing ouputs.
         */
        get onDidLayoutChange() {
            return this.component.onDidLayoutChange;
        }
        /**
         * Invoked when a view is added.
         */
        get onDidAddView() {
            return this.component.onDidAddView;
        }
        /**
         * Invoked when a view is removed.
         */
        get onDidRemoveView() {
            return this.component.onDidRemoveView;
        }
        constructor(component) {
            this.component = component;
        }
        /**
         * Removes an existing panel and optionally provide a `Sizing` method
         * for the subsequent resize.
         */
        removePanel(panel, sizing) {
            this.component.removePanel(panel, sizing);
        }
        /**
         * Focus the component.
         */
        focus() {
            this.component.focus();
        }
        /**
         * Get the reference to a panel given it's `string` id.
         */
        getPanel(id) {
            return this.component.getPanel(id);
        }
        /**
         * Layout the panel with a width and height.
         */
        layout(width, height) {
            return this.component.layout(width, height);
        }
        /**
         * Add a new panel and return the created instance.
         */
        addPanel(options) {
            return this.component.addPanel(options);
        }
        /**
         * Move a panel given it's current and desired index.
         */
        movePanel(from, to) {
            this.component.movePanel(from, to);
        }
        /**
         * Deserialize a layout to built a splitivew.
         */
        fromJSON(data) {
            this.component.fromJSON(data);
        }
        /** Serialize a layout */
        toJSON() {
            return this.component.toJSON();
        }
        /**
         * Remove all panels and clear the component.
         */
        clear() {
            this.component.clear();
        }
        /**
         * Update configuratable options.
         */
        updateOptions(options) {
            this.component.updateOptions(options);
        }
        /**
         * Release resources and teardown component. Do not call when using framework versions of dockview.
         */
        dispose() {
            this.component.dispose();
        }
    }
    class PaneviewApi {
        /**
         * The minimum size  the component can reach where size is measured in the direction of orientation provided.
         */
        get minimumSize() {
            return this.component.minimumSize;
        }
        /**
         * The maximum size the component can reach where size is measured in the direction of orientation provided.
         */
        get maximumSize() {
            return this.component.maximumSize;
        }
        /**
         * Width of the component.
         */
        get width() {
            return this.component.width;
        }
        /**
         * Height of the component.
         */
        get height() {
            return this.component.height;
        }
        /**
         * All panel objects.
         */
        get panels() {
            return this.component.panels;
        }
        /**
         * Invoked when any layout change occures, an aggregation of many events.
         */
        get onDidLayoutChange() {
            return this.component.onDidLayoutChange;
        }
        /**
         * Invoked after a layout is deserialzied using the `fromJSON` method.
         */
        get onDidLayoutFromJSON() {
            return this.component.onDidLayoutFromJSON;
        }
        /**
         * Invoked when a panel is added. May be called multiple times when moving panels.
         */
        get onDidAddView() {
            return this.component.onDidAddView;
        }
        /**
         * Invoked when a panel is removed. May be called multiple times when moving panels.
         */
        get onDidRemoveView() {
            return this.component.onDidRemoveView;
        }
        /**
         * Invoked when a Drag'n'Drop event occurs that the component was unable to handle. Exposed for custom Drag'n'Drop functionality.
         */
        get onDidDrop() {
            return this.component.onDidDrop;
        }
        get onUnhandledDragOverEvent() {
            return this.component.onUnhandledDragOverEvent;
        }
        constructor(component) {
            this.component = component;
        }
        /**
         * Remove a panel given the panel object.
         */
        removePanel(panel) {
            this.component.removePanel(panel);
        }
        /**
         * Get a panel object given a `string` id. May return `undefined`.
         */
        getPanel(id) {
            return this.component.getPanel(id);
        }
        /**
         * Move a panel given it's current and desired index.
         */
        movePanel(from, to) {
            this.component.movePanel(from, to);
        }
        /**
         *  Focus the component. Will try to focus an active panel if one exists.
         */
        focus() {
            this.component.focus();
        }
        /**
         * Force resize the component to an exact width and height. Read about auto-resizing before using.
         */
        layout(width, height) {
            this.component.layout(width, height);
        }
        /**
         * Add a panel and return the created object.
         */
        addPanel(options) {
            return this.component.addPanel(options);
        }
        /**
         * Create a component from a serialized object.
         */
        fromJSON(data) {
            this.component.fromJSON(data);
        }
        /**
         * Create a serialized object of the current component.
         */
        toJSON() {
            return this.component.toJSON();
        }
        /**
         * Reset the component back to an empty and default state.
         */
        clear() {
            this.component.clear();
        }
        /**
         * Update configuratable options.
         */
        updateOptions(options) {
            this.component.updateOptions(options);
        }
        /**
         * Release resources and teardown component. Do not call when using framework versions of dockview.
         */
        dispose() {
            this.component.dispose();
        }
    }
    class GridviewApi {
        /**
         * Width of the component.
         */
        get width() {
            return this.component.width;
        }
        /**
         * Height of the component.
         */
        get height() {
            return this.component.height;
        }
        /**
         * Minimum height of the component.
         */
        get minimumHeight() {
            return this.component.minimumHeight;
        }
        /**
         * Maximum height of the component.
         */
        get maximumHeight() {
            return this.component.maximumHeight;
        }
        /**
         * Minimum width of the component.
         */
        get minimumWidth() {
            return this.component.minimumWidth;
        }
        /**
         * Maximum width of the component.
         */
        get maximumWidth() {
            return this.component.maximumWidth;
        }
        /**
         * Invoked when any layout change occures, an aggregation of many events.
         */
        get onDidLayoutChange() {
            return this.component.onDidLayoutChange;
        }
        /**
         * Invoked when a panel is added. May be called multiple times when moving panels.
         */
        get onDidAddPanel() {
            return this.component.onDidAddGroup;
        }
        /**
         * Invoked when a panel is removed. May be called multiple times when moving panels.
         */
        get onDidRemovePanel() {
            return this.component.onDidRemoveGroup;
        }
        /**
         * Invoked when the active panel changes. May be undefined if no panel is active.
         */
        get onDidActivePanelChange() {
            return this.component.onDidActiveGroupChange;
        }
        /**
         * Invoked after a layout is deserialzied using the `fromJSON` method.
         */
        get onDidLayoutFromJSON() {
            return this.component.onDidLayoutFromJSON;
        }
        /**
         * All panel objects.
         */
        get panels() {
            return this.component.groups;
        }
        /**
         * Current orientation. Can be changed after initialization.
         */
        get orientation() {
            return this.component.orientation;
        }
        set orientation(value) {
            this.component.updateOptions({ orientation: value });
        }
        constructor(component) {
            this.component = component;
        }
        /**
         *  Focus the component. Will try to focus an active panel if one exists.
         */
        focus() {
            this.component.focus();
        }
        /**
         * Force resize the component to an exact width and height. Read about auto-resizing before using.
         */
        layout(width, height, force = false) {
            this.component.layout(width, height, force);
        }
        /**
         * Add a panel and return the created object.
         */
        addPanel(options) {
            return this.component.addPanel(options);
        }
        /**
         * Remove a panel given the panel object.
         */
        removePanel(panel, sizing) {
            this.component.removePanel(panel, sizing);
        }
        /**
         * Move a panel in a particular direction relative to another panel.
         */
        movePanel(panel, options) {
            this.component.movePanel(panel, options);
        }
        /**
         * Get a panel object given a `string` id. May return `undefined`.
         */
        getPanel(id) {
            return this.component.getPanel(id);
        }
        /**
         * Create a component from a serialized object.
         */
        fromJSON(data) {
            return this.component.fromJSON(data);
        }
        /**
         * Create a serialized object of the current component.
         */
        toJSON() {
            return this.component.toJSON();
        }
        /**
         * Reset the component back to an empty and default state.
         */
        clear() {
            this.component.clear();
        }
        updateOptions(options) {
            this.component.updateOptions(options);
        }
        /**
         * Release resources and teardown component. Do not call when using framework versions of dockview.
         */
        dispose() {
            this.component.dispose();
        }
    }
    class DockviewApi {
        /**
         * The unique identifier for this instance. Used to manage scope of Drag'n'Drop events.
         */
        get id() {
            return this.component.id;
        }
        /**
         * Width of the component.
         */
        get width() {
            return this.component.width;
        }
        /**
         * Height of the component.
         */
        get height() {
            return this.component.height;
        }
        /**
         * Minimum height of the component.
         */
        get minimumHeight() {
            return this.component.minimumHeight;
        }
        /**
         * Maximum height of the component.
         */
        get maximumHeight() {
            return this.component.maximumHeight;
        }
        /**
         * Minimum width of the component.
         */
        get minimumWidth() {
            return this.component.minimumWidth;
        }
        /**
         * Maximum width of the component.
         */
        get maximumWidth() {
            return this.component.maximumWidth;
        }
        /**
         * Total number of groups.
         */
        get size() {
            return this.component.size;
        }
        /**
         * The active tab-group color palette. Reflects the configured
         * `tabGroupColors` option, or the built-in defaults when unset.
         * Useful for custom chip renderers that want to roll their own
         * picker UI.
         */
        get tabGroupColors() {
            return this.component.tabGroupColorPalette.entries();
        }
        /**
         * Total number of panels.
         */
        get totalPanels() {
            return this.component.totalPanels;
        }
        /**
         * Invoked when the active group changes. May be undefined if no group is active.
         */
        get onDidActiveGroupChange() {
            return this.component.onDidActiveGroupChange;
        }
        /**
         * Invoked when a group is added. May be called multiple times when moving groups.
         */
        get onDidAddGroup() {
            return this.component.onDidAddGroup;
        }
        /**
         * Invoked when a group is removed. May be called multiple times when moving groups.
         */
        get onDidRemoveGroup() {
            return this.component.onDidRemoveGroup;
        }
        /**
         * Invoked when the active panel changes. May be undefined if no panel is active.
         */
        get onDidActivePanelChange() {
            return this.component.onDidActivePanelChange;
        }
        /**
         * Invoked when a panel is added. May be called multiple times when moving panels.
         */
        get onDidAddPanel() {
            return this.component.onDidAddPanel;
        }
        /**
         * Invoked when a panel is removed. May be called multiple times when moving panels.
         */
        get onDidRemovePanel() {
            return this.component.onDidRemovePanel;
        }
        get onDidMovePanel() {
            return this.component.onDidMovePanel;
        }
        /**
         * Invoked after a layout is deserialzied using the `fromJSON` method.
         */
        get onDidLayoutFromJSON() {
            return this.component.onDidLayoutFromJSON;
        }
        /**
         * Invoked when any layout change occures, an aggregation of many events.
         */
        get onDidLayoutChange() {
            return this.component.onDidLayoutChange;
        }
        /**
         * Invoked when a Drag'n'Drop event occurs that the component was unable to handle. Exposed for custom Drag'n'Drop functionality.
         */
        get onDidDrop() {
            return this.component.onDidDrop;
        }
        /**
         * Invoked when a Drag'n'Drop event occurs but before dockview handles it giving the user an opportunity to intecept and
         * prevent the event from occuring using the standard `preventDefault()` syntax.
         *
         * Preventing certain events may causes unexpected behaviours, use carefully.
         */
        get onWillDrop() {
            return this.component.onWillDrop;
        }
        /**
         * Invoked before an overlay is shown indicating a drop target.
         *
         * Calling `event.preventDefault()` will prevent the overlay being shown and prevent
         * the any subsequent drop event.
         */
        get onWillShowOverlay() {
            return this.component.onWillShowOverlay;
        }
        /**
         * Invoked before a group is dragged.
         *
         * Calling `event.nativeEvent.preventDefault()` will prevent the group drag starting.
         *
         */
        get onWillDragGroup() {
            return this.component.onWillDragGroup;
        }
        /**
         * Invoked before a panel is dragged.
         *
         * Calling `event.nativeEvent.preventDefault()` will prevent the panel drag starting.
         */
        get onWillDragPanel() {
            return this.component.onWillDragPanel;
        }
        get onUnhandledDragOverEvent() {
            return this.component.onUnhandledDragOverEvent;
        }
        get onDidPopoutGroupSizeChange() {
            return this.component.onDidPopoutGroupSizeChange;
        }
        get onDidPopoutGroupPositionChange() {
            return this.component.onDidPopoutGroupPositionChange;
        }
        get onDidOpenPopoutWindowFail() {
            return this.component.onDidOpenPopoutWindowFail;
        }
        /**
         * Invoked when a tab group is created in any group.
         */
        get onDidCreateTabGroup() {
            return this.component.onDidCreateTabGroup;
        }
        /**
         * Invoked when a tab group is destroyed in any group.
         */
        get onDidDestroyTabGroup() {
            return this.component.onDidDestroyTabGroup;
        }
        /**
         * Invoked when a panel is added to a tab group.
         */
        get onDidAddPanelToTabGroup() {
            return this.component.onDidAddPanelToTabGroup;
        }
        /**
         * Invoked when a panel is removed from a tab group.
         */
        get onDidRemovePanelFromTabGroup() {
            return this.component.onDidRemovePanelFromTabGroup;
        }
        /**
         * Invoked when a tab group's properties (label, color) change.
         */
        get onDidTabGroupChange() {
            return this.component.onDidTabGroupChange;
        }
        /**
         * Invoked when a tab group is collapsed or expanded.
         */
        get onDidTabGroupCollapsedChange() {
            return this.component.onDidTabGroupCollapsedChange;
        }
        /**
         * All panel objects.
         */
        get panels() {
            return this.component.panels;
        }
        /**
         * All group objects.
         */
        get groups() {
            return this.component.groups;
        }
        /**
         *  Active panel object.
         */
        get activePanel() {
            return this.component.activePanel;
        }
        /**
         * Active group object.
         */
        get activeGroup() {
            return this.component.activeGroup;
        }
        constructor(component) {
            this.component = component;
        }
        /**
         *  Focus the component. Will try to focus an active panel if one exists.
         */
        focus() {
            this.component.focus();
        }
        /**
         * Get a panel object given a `string` id. May return `undefined`.
         */
        getPanel(id) {
            return this.component.getGroupPanel(id);
        }
        /**
         * Force resize the component to an exact width and height. Read about auto-resizing before using.
         */
        layout(width, height, force = false) {
            this.component.layout(width, height, force);
        }
        /**
         * Add a panel and return the created object.
         */
        addPanel(options) {
            return this.component.addPanel(options);
        }
        /**
         * Remove a panel given the panel object.
         */
        removePanel(panel) {
            this.component.removePanel(panel);
        }
        /**
         * Add a group and return the created object.
         */
        addGroup(options) {
            return this.component.addGroup(options);
        }
        /**
         * Close all groups and panels.
         */
        closeAllGroups() {
            return this.component.closeAllGroups();
        }
        /**
         * Remove a group and any panels within the group.
         */
        removeGroup(group) {
            this.component.removeGroup(group);
        }
        /**
         * Get a group object given a `string` id. May return undefined.
         */
        getGroup(id) {
            return this.component.getPanel(id);
        }
        /**
         * Add a floating group
         */
        addFloatingGroup(item, options) {
            return this.component.addFloatingGroup(item, options);
        }
        /**
         * Create a component from a serialized object.
         */
        fromJSON(data, options) {
            this.component.fromJSON(data, options);
        }
        /**
         * Create a serialized object of the current component.
         */
        toJSON() {
            return this.component.toJSON();
        }
        /**
         * Reset the component back to an empty and default state.
         */
        clear() {
            this.component.clear();
        }
        /**
         * Move the focus progmatically to the next panel or group.
         */
        moveToNext(options) {
            this.component.moveToNext(options);
        }
        /**
         * Move the focus progmatically to the previous panel or group.
         */
        moveToPrevious(options) {
            this.component.moveToPrevious(options);
        }
        maximizeGroup(panel) {
            this.component.maximizeGroup(panel.group);
        }
        hasMaximizedGroup() {
            return this.component.hasMaximizedGroup();
        }
        exitMaximizedGroup() {
            this.component.exitMaximizedGroup();
        }
        get onDidMaximizedGroupChange() {
            return this.component.onDidMaximizedGroupChange;
        }
        /**
         * Add a popout group in a new Window
         */
        addPopoutGroup(item, options) {
            return this.component.addPopoutGroup(item, options);
        }
        /**
         * Add an edge group at the given position. Returns the group panel API
         * for the newly created group. Throws if a group already exists there.
         */
        addEdgeGroup(position, options) {
            return this.component.addEdgeGroup(position, options);
        }
        /**
         * Get the group panel API for an edge group at the given position.
         * Returns `undefined` if no edge group is configured at that position.
         */
        getEdgeGroup(position) {
            return this.component.getEdgeGroup(position);
        }
        /**
         * Set the visibility of an edge group.
         */
        setEdgeGroupVisible(position, visible) {
            this.component.setEdgeGroupVisible(position, visible);
        }
        /**
         * Check whether an edge group is currently visible.
         */
        isEdgeGroupVisible(position) {
            return this.component.isEdgeGroupVisible(position);
        }
        /**
         * Remove an edge group and reclaim its slot in the layout.
         * All panels inside the group are disposed. Throws if no group exists at position.
         */
        removeEdgeGroup(position) {
            this.component.removeEdgeGroup(position);
        }
        updateOptions(options) {
            this.component.updateOptions(options);
        }
        // === Tab Group API ===
        _getGroupModel(groupId) {
            const group = this.component.getPanel(groupId);
            if (!group) {
                throw new Error(`dockview: group '${groupId}' not found`);
            }
            return group.model;
        }
        createTabGroup(options) {
            const model = this._getGroupModel(options.groupId);
            return model.createTabGroup({
                label: options.label,
                color: options.color,
                componentParams: options.componentParams,
            });
        }
        dissolveTabGroup(options) {
            const model = this._getGroupModel(options.groupId);
            model.dissolveTabGroup(options.tabGroupId);
        }
        addPanelToTabGroup(options) {
            const model = this._getGroupModel(options.groupId);
            model.addPanelToTabGroup(options.tabGroupId, options.panelId, options.index);
        }
        removePanelFromTabGroup(options) {
            const model = this._getGroupModel(options.groupId);
            model.removePanelFromTabGroup(options.panelId);
        }
        getTabGroups(options) {
            const model = this._getGroupModel(options.groupId);
            return model.getTabGroups();
        }
        getTabGroupForPanel(options) {
            const model = this._getGroupModel(options.groupId);
            return model.getTabGroupForPanel(options.panelId);
        }
        moveTabGroup(options) {
            const model = this._getGroupModel(options.groupId);
            model.moveTabGroup(options.tabGroupId, options.index);
        }
        /**
         * Release resources and teardown component. Do not call when using framework versions of dockview.
         */
        dispose() {
            this.component.dispose();
        }
    }

    class DragAndDropObserver extends CompositeDisposable {
        constructor(element, callbacks) {
            super();
            this.element = element;
            this.callbacks = callbacks;
            this.target = null;
            this.registerListeners();
        }
        onDragEnter(e) {
            this.target = e.target;
            this.callbacks.onDragEnter(e);
        }
        onDragOver(e) {
            e.preventDefault(); // needed so that the drop event fires (https://stackoverflow.com/questions/21339924/drop-event-not-firing-in-chrome)
            if (this.callbacks.onDragOver) {
                this.callbacks.onDragOver(e);
            }
        }
        onDragLeave(e) {
            if (this.target === e.target) {
                this.target = null;
                this.callbacks.onDragLeave(e);
            }
        }
        onDragEnd(e) {
            this.target = null;
            this.callbacks.onDragEnd(e);
        }
        onDrop(e) {
            this.callbacks.onDrop(e);
        }
        registerListeners() {
            this.addDisposables(addDisposableListener(this.element, 'dragenter', (e) => {
                this.onDragEnter(e);
            }, true));
            this.addDisposables(addDisposableListener(this.element, 'dragover', (e) => {
                this.onDragOver(e);
            }, true));
            this.addDisposables(addDisposableListener(this.element, 'dragleave', (e) => {
                this.onDragLeave(e);
            }));
            this.addDisposables(addDisposableListener(this.element, 'dragend', (e) => {
                this.onDragEnd(e);
            }));
            this.addDisposables(addDisposableListener(this.element, 'drop', (e) => {
                this.onDrop(e);
            }));
        }
    }

    // Two render paths: in-place (dropzone appended to drop element) and
    // anchored (overlay rendered into an external anchor container).
    const DEFAULT_SIZE = { value: 50, type: 'percentage' };
    const SMALL_WIDTH_BOUNDARY = 100;
    const SMALL_HEIGHT_BOUNDARY = 100;
    function createOverlayElements() {
        const dropzone = document.createElement('div');
        dropzone.className = 'dv-drop-target-dropzone';
        const selection = document.createElement('div');
        selection.className = 'dv-drop-target-selection';
        dropzone.appendChild(selection);
        return { dropzone, selection };
    }
    function computeOverlayShape(quadrant, width, height, overlayModel) {
        var _a, _b, _c;
        const smallWidthBoundary = (_a = overlayModel === null || overlayModel === void 0 ? void 0 : overlayModel.smallWidthBoundary) !== null && _a !== void 0 ? _a : SMALL_WIDTH_BOUNDARY;
        const smallHeightBoundary = (_b = overlayModel === null || overlayModel === void 0 ? void 0 : overlayModel.smallHeightBoundary) !== null && _b !== void 0 ? _b : SMALL_HEIGHT_BOUNDARY;
        const isSmallX = width < smallWidthBoundary;
        const isSmallY = height < smallHeightBoundary;
        const isLeft = quadrant === 'left';
        const isRight = quadrant === 'right';
        const isTop = quadrant === 'top';
        const isBottom = quadrant === 'bottom';
        const rightClass = !isSmallX && isRight;
        const leftClass = !isSmallX && isLeft;
        const topClass = !isSmallY && isTop;
        const bottomClass = !isSmallY && isBottom;
        let size = 1;
        const sizeOptions = (_c = overlayModel === null || overlayModel === void 0 ? void 0 : overlayModel.size) !== null && _c !== void 0 ? _c : DEFAULT_SIZE;
        if (sizeOptions.type === 'percentage') {
            size = clamp(sizeOptions.value, 0, 100) / 100;
        }
        else {
            if (rightClass || leftClass) {
                size = clamp(0, sizeOptions.value, width) / width;
            }
            if (topClass || bottomClass) {
                size = clamp(0, sizeOptions.value, height) / height;
            }
        }
        return {
            isSmallX,
            isSmallY,
            isLeft,
            isRight,
            isTop,
            isBottom,
            rightClass,
            leftClass,
            topClass,
            bottomClass,
            size,
        };
    }
    function renderInPlaceOverlay(overlay, quadrant, width, height, overlayModel) {
        const shape = computeOverlayShape(quadrant, width, height, overlayModel);
        const { rightClass, leftClass, topClass, bottomClass, size } = shape;
        const box = { top: '0px', left: '0px', width: '100%', height: '100%' };
        if (rightClass) {
            box.left = `${100 * (1 - size)}%`;
            box.width = `${100 * size}%`;
        }
        else if (leftClass) {
            box.width = `${100 * size}%`;
        }
        else if (topClass) {
            box.height = `${100 * size}%`;
        }
        else if (bottomClass) {
            box.top = `${100 * (1 - size)}%`;
            box.height = `${100 * size}%`;
        }
        if (shape.isSmallX && shape.isLeft) {
            box.width = '4px';
        }
        if (shape.isSmallX && shape.isRight) {
            box.left = `${width - 4}px`;
            box.width = '4px';
        }
        if (shape.isSmallY && shape.isTop) {
            box.height = '4px';
        }
        if (shape.isSmallY && shape.isBottom) {
            box.top = `${height - 4}px`;
            box.height = '4px';
        }
        overlay.style.top = box.top;
        overlay.style.left = box.left;
        overlay.style.width = box.width;
        overlay.style.height = box.height;
        overlay.style.visibility = 'visible';
        if (!overlay.style.transform || overlay.style.transform === '') {
            overlay.style.transform = 'translate3d(0, 0, 0)';
        }
        const isLine = (shape.isSmallX && (shape.isLeft || shape.isRight)) ||
            (shape.isSmallY && (shape.isTop || shape.isBottom));
        toggleClass(overlay, 'dv-drop-target-small-vertical', shape.isSmallY);
        toggleClass(overlay, 'dv-drop-target-small-horizontal', shape.isSmallX);
        toggleClass(overlay, 'dv-drop-target-selection-line', isLine);
        toggleClass(overlay, 'dv-drop-target-left', shape.isLeft);
        toggleClass(overlay, 'dv-drop-target-right', shape.isRight);
        toggleClass(overlay, 'dv-drop-target-top', shape.isTop);
        toggleClass(overlay, 'dv-drop-target-bottom', shape.isBottom);
        toggleClass(overlay, 'dv-drop-target-center', quadrant === 'center');
    }
    function checkAnchoredBoundsChanged(overlay, bounds) {
        const topPx = `${Math.round(bounds.top)}px`;
        const leftPx = `${Math.round(bounds.left)}px`;
        const widthPx = `${Math.round(bounds.width)}px`;
        const heightPx = `${Math.round(bounds.height)}px`;
        return (overlay.style.top !== topPx ||
            overlay.style.left !== leftPx ||
            overlay.style.width !== widthPx ||
            overlay.style.height !== heightPx);
    }
    function applyAnchoredBounds(overlay, bounds) {
        overlay.style.top = `${Math.round(bounds.top)}px`;
        overlay.style.left = `${Math.round(bounds.left)}px`;
        overlay.style.width = `${Math.round(bounds.width)}px`;
        overlay.style.height = `${Math.round(bounds.height)}px`;
        overlay.style.visibility = 'visible';
        if (!overlay.style.transform || overlay.style.transform === '') {
            overlay.style.transform = 'translate3d(0, 0, 0)';
        }
    }
    /** `boundsChanged: false` lets callers skip redundant work on tight drag loops. */
    function renderAnchoredOverlay(args) {
        const shape = computeOverlayShape(args.quadrant, args.width, args.height, args.overlayModel);
        const { rightClass, leftClass, topClass, bottomClass, size } = shape;
        const elBox = args.outlineElement.getBoundingClientRect();
        const ta = args.targetModel.getElements(undefined, args.outlineElement);
        const el = ta.root;
        const overlay = ta.overlay;
        const bigbox = el.getBoundingClientRect();
        const rootTop = elBox.top - bigbox.top;
        const rootLeft = elBox.left - bigbox.left;
        const box = {
            top: rootTop,
            left: rootLeft,
            width: args.width,
            height: args.height,
        };
        if (rightClass) {
            box.left = rootLeft + args.width * (1 - size);
            box.width = args.width * size;
        }
        else if (leftClass) {
            box.width = args.width * size;
        }
        else if (topClass) {
            box.height = args.height * size;
        }
        else if (bottomClass) {
            box.top = rootTop + args.height * (1 - size);
            box.height = args.height * size;
        }
        if (shape.isSmallX && shape.isLeft) {
            box.width = 4;
        }
        if (shape.isSmallX && shape.isRight) {
            box.left = rootLeft + args.width - 4;
            box.width = 4;
        }
        if (shape.isSmallY && shape.isTop) {
            box.height = 4;
        }
        if (shape.isSmallY && shape.isBottom) {
            box.top = rootTop + args.height - 4;
            box.height = 4;
        }
        if (!checkAnchoredBoundsChanged(overlay, box)) {
            return { boundsChanged: false, targetChanged: ta.changed };
        }
        applyAnchoredBounds(overlay, box);
        overlay.className = `dv-drop-target-anchor${args.className ? ` ${args.className}` : ''}`;
        toggleClass(overlay, 'dv-drop-target-left', shape.isLeft);
        toggleClass(overlay, 'dv-drop-target-right', shape.isRight);
        toggleClass(overlay, 'dv-drop-target-top', shape.isTop);
        toggleClass(overlay, 'dv-drop-target-bottom', shape.isBottom);
        toggleClass(overlay, 'dv-drop-target-anchor-line', (shape.isSmallX && (shape.isLeft || shape.isRight)) ||
            (shape.isSmallY && (shape.isTop || shape.isBottom)));
        toggleClass(overlay, 'dv-drop-target-center', args.quadrant === 'center');
        if (ta.changed) {
            toggleClass(overlay, 'dv-drop-target-anchor-container-changed', true);
            setTimeout(() => {
                toggleClass(overlay, 'dv-drop-target-anchor-container-changed', false);
            }, 10);
        }
        return { boundsChanged: true, targetChanged: ta.changed };
    }

    class WillShowOverlayEvent extends DockviewEvent {
        get nativeEvent() {
            return this.options.nativeEvent;
        }
        get position() {
            return this.options.position;
        }
        constructor(options) {
            super();
            this.options = options;
        }
    }
    function directionToPosition(direction) {
        switch (direction) {
            case 'above':
                return 'top';
            case 'below':
                return 'bottom';
            case 'left':
                return 'left';
            case 'right':
                return 'right';
            case 'within':
                return 'center';
            default:
                throw new Error(`invalid direction '${direction}'`);
        }
    }
    function positionToDirection(position) {
        switch (position) {
            case 'top':
                return 'above';
            case 'bottom':
                return 'below';
            case 'left':
                return 'left';
            case 'right':
                return 'right';
            case 'center':
                return 'within';
            default:
                throw new Error(`invalid position '${position}'`);
        }
    }
    const DEFAULT_ACTIVATION_SIZE$1 = {
        value: 20,
        type: 'percentage',
    };
    class Droptarget extends CompositeDisposable {
        get disabled() {
            return this._disabled;
        }
        set disabled(value) {
            this._disabled = value;
        }
        get state() {
            return this._state;
        }
        constructor(element, options) {
            super();
            this.element = element;
            this.options = options;
            this._onDrop = new Emitter();
            this.onDrop = this._onDrop.event;
            this._onWillShowOverlay = new Emitter();
            this.onWillShowOverlay = this._onWillShowOverlay.event;
            this._disabled = false;
            // use a set to take advantage of #<set>.has
            this._acceptedTargetZonesSet = new Set(this.options.acceptedTargetZones);
            this.dnd = new DragAndDropObserver(this.element, {
                onDragEnter: () => {
                    var _a, _b, _c;
                    (_c = (_b = (_a = this.options).getOverrideTarget) === null || _b === void 0 ? void 0 : _b.call(_a)) === null || _c === void 0 ? void 0 : _c.getElements();
                },
                onDragOver: (e) => {
                    var _a, _b, _c, _d, _e, _f, _g;
                    Droptarget.ACTUAL_TARGET = this;
                    const overrideTarget = (_b = (_a = this.options).getOverrideTarget) === null || _b === void 0 ? void 0 : _b.call(_a);
                    if (this._acceptedTargetZonesSet.size === 0) {
                        if (overrideTarget) {
                            return;
                        }
                        this.removeDropTarget();
                        return;
                    }
                    const target = (_e = (_d = (_c = this.options).getOverlayOutline) === null || _d === void 0 ? void 0 : _d.call(_c)) !== null && _e !== void 0 ? _e : this.element;
                    const width = target.offsetWidth;
                    const height = target.offsetHeight;
                    if (width === 0 || height === 0) {
                        return; // avoid div!0
                    }
                    const rect = e.currentTarget.getBoundingClientRect();
                    const x = ((_f = e.clientX) !== null && _f !== void 0 ? _f : 0) - rect.left;
                    const y = ((_g = e.clientY) !== null && _g !== void 0 ? _g : 0) - rect.top;
                    const quadrant = this.calculateQuadrant(this._acceptedTargetZonesSet, x, y, width, height);
                    /**
                     * If the event has already been used by another DropTarget instance
                     * then don't show a second drop target, only one target should be
                     * active at any one time
                     */
                    if (this.isAlreadyUsed(e) || quadrant === null) {
                        // no drop target should be displayed
                        this.removeDropTarget();
                        return;
                    }
                    if (!this.options.canDisplayOverlay(e, quadrant)) {
                        if (overrideTarget) {
                            return;
                        }
                        this.removeDropTarget();
                        return;
                    }
                    const willShowOverlayEvent = new WillShowOverlayEvent({
                        nativeEvent: e,
                        position: quadrant,
                    });
                    /**
                     * Provide an opportunity to prevent the overlay appearing and in turn
                     * any dnd behaviours
                     */
                    this._onWillShowOverlay.fire(willShowOverlayEvent);
                    if (willShowOverlayEvent.defaultPrevented) {
                        this.removeDropTarget();
                        return;
                    }
                    this.markAsUsed(e);
                    if (overrideTarget) ;
                    else if (!this.targetElement) {
                        const els = createOverlayElements();
                        this.targetElement = els.dropzone;
                        this.overlayElement = els.selection;
                        this._state = 'center';
                        target.classList.add('dv-drop-target');
                        target.append(this.targetElement);
                    }
                    this.toggleClasses(quadrant, width, height);
                    this._state = quadrant;
                },
                onDragLeave: () => {
                    var _a, _b;
                    const target = (_b = (_a = this.options).getOverrideTarget) === null || _b === void 0 ? void 0 : _b.call(_a);
                    if (target) {
                        return;
                    }
                    this.removeDropTarget();
                },
                onDragEnd: (e) => {
                    var _a, _b;
                    const target = (_b = (_a = this.options).getOverrideTarget) === null || _b === void 0 ? void 0 : _b.call(_a);
                    if (target && Droptarget.ACTUAL_TARGET === this) {
                        if (this._state) {
                            // only stop the propagation of the event if we are dealing with it
                            // which is only when the target has state
                            e.stopPropagation();
                            this._onDrop.fire({
                                position: this._state,
                                nativeEvent: e,
                            });
                        }
                    }
                    this.removeDropTarget();
                    target === null || target === void 0 ? void 0 : target.clear();
                },
                onDrop: (e) => {
                    var _a, _b, _c;
                    e.preventDefault();
                    const state = this._state;
                    this.removeDropTarget();
                    (_c = (_b = (_a = this.options).getOverrideTarget) === null || _b === void 0 ? void 0 : _b.call(_a)) === null || _c === void 0 ? void 0 : _c.clear();
                    if (state) {
                        // only stop the propagation of the event if we are dealing with it
                        // which is only when the target has state
                        e.stopPropagation();
                        this._onDrop.fire({ position: state, nativeEvent: e });
                    }
                },
            });
            this.addDisposables(this._onDrop, this._onWillShowOverlay, this.dnd);
        }
        setTargetZones(acceptedTargetZones) {
            this._acceptedTargetZonesSet = new Set(acceptedTargetZones);
        }
        setOverlayModel(model) {
            this.options.overlayModel = model;
        }
        dispose() {
            this.removeDropTarget();
            super.dispose();
        }
        /**
         * Add a property to the event object for other potential listeners to check
         */
        markAsUsed(event) {
            event[Droptarget.USED_EVENT_ID] = true;
        }
        /**
         * Check is the event has already been used by another instance of DropTarget
         */
        isAlreadyUsed(event) {
            const value = event[Droptarget.USED_EVENT_ID];
            return typeof value === 'boolean' && value;
        }
        toggleClasses(quadrant, width, height) {
            var _a, _b, _c, _d, _e;
            const target = (_b = (_a = this.options).getOverrideTarget) === null || _b === void 0 ? void 0 : _b.call(_a);
            if (target) {
                const outlineEl = (_e = (_d = (_c = this.options).getOverlayOutline) === null || _d === void 0 ? void 0 : _d.call(_c)) !== null && _e !== void 0 ? _e : this.element;
                renderAnchoredOverlay({
                    outlineElement: outlineEl,
                    targetModel: target,
                    quadrant,
                    width,
                    height,
                    overlayModel: this.options.overlayModel,
                    className: this.options.className,
                });
                return;
            }
            if (!this.overlayElement) {
                return;
            }
            renderInPlaceOverlay(this.overlayElement, quadrant, width, height, this.options.overlayModel);
        }
        calculateQuadrant(overlayType, x, y, width, height) {
            var _a, _b;
            const activationSizeOptions = (_b = (_a = this.options.overlayModel) === null || _a === void 0 ? void 0 : _a.activationSize) !== null && _b !== void 0 ? _b : DEFAULT_ACTIVATION_SIZE$1;
            const isPercentage = activationSizeOptions.type === 'percentage';
            if (isPercentage) {
                return calculateQuadrantAsPercentage(overlayType, x, y, width, height, activationSizeOptions.value);
            }
            return calculateQuadrantAsPixels(overlayType, x, y, width, height, activationSizeOptions.value);
        }
        removeDropTarget() {
            var _a;
            if (this.targetElement) {
                this._state = undefined;
                (_a = this.targetElement.parentElement) === null || _a === void 0 ? void 0 : _a.classList.remove('dv-drop-target');
                this.targetElement.remove();
                this.targetElement = undefined;
                this.overlayElement = undefined;
            }
        }
    }
    Droptarget.USED_EVENT_ID = '__dockview_droptarget_event_is_used__';
    function calculateQuadrantAsPercentage(overlayType, x, y, width, height, threshold) {
        const xp = (100 * x) / width;
        const yp = (100 * y) / height;
        if (overlayType.has('left') && xp < threshold) {
            return 'left';
        }
        if (overlayType.has('right') && xp > 100 - threshold) {
            return 'right';
        }
        if (overlayType.has('top') && yp < threshold) {
            return 'top';
        }
        if (overlayType.has('bottom') && yp > 100 - threshold) {
            return 'bottom';
        }
        if (!overlayType.has('center')) {
            return null;
        }
        return 'center';
    }
    function calculateQuadrantAsPixels(overlayType, x, y, width, height, threshold) {
        if (overlayType.has('left') && x < threshold) {
            return 'left';
        }
        if (overlayType.has('right') && x > width - threshold) {
            return 'right';
        }
        if (overlayType.has('top') && y < threshold) {
            return 'top';
        }
        if (overlayType.has('bottom') && y > height - threshold) {
            return 'bottom';
        }
        if (!overlayType.has('center')) {
            return null;
        }
        return 'center';
    }

    function addGhostImage(dataTransfer, ghostElement, options) {
        var _a, _b;
        // class dockview provides to force ghost image to be drawn on a different layer and prevent weird rendering issues
        addClasses(ghostElement, 'dv-dragged');
        // move the element off-screen initially otherwise it may in some cases be rendered at (0,0) momentarily
        ghostElement.style.top = '-9999px';
        document.body.appendChild(ghostElement);
        dataTransfer.setDragImage(ghostElement, (_a = options === null || options === void 0 ? void 0 : options.x) !== null && _a !== void 0 ? _a : 0, (_b = options === null || options === void 0 ? void 0 : options.y) !== null && _b !== void 0 ? _b : 0);
        setTimeout(() => {
            removeClasses(ghostElement, 'dv-dragged');
            ghostElement.remove();
        }, 0);
    }

    /**
     * Singleton — only one pointer-driven drag active at a time.
     *
     * State is shared across every Dockview instance on the page. Targets
     * from instance B receive hit-tests from drags originating in instance A;
     * that's intentional for cross-instance drops since `LocalSelectionTransfer`
     * is also process-wide. The corollary is that every Tabs subscriber to
     * `onDragMove` fires for every pointer drag globally — each subscriber
     * hit-tests against its own DOM, so this is O(N) per pointermove where N
     * is the number of registered listeners across all instances.
     */
    class PointerDragController extends CompositeDisposable {
        static getInstance() {
            if (!PointerDragController._instance) {
                PointerDragController._instance = new PointerDragController();
            }
            return PointerDragController._instance;
        }
        constructor() {
            super();
            this._targets = new Set();
            /** Kept in sync with `_targets` so hit-testing is allocation-free. */
            this._targetByElement = new Map();
            this._onDragStart = new Emitter();
            this.onDragStart = this._onDragStart.event;
            this._onDragMove = new Emitter();
            this.onDragMove = this._onDragMove.event;
            this._onDragEnd = new Emitter();
            this.onDragEnd = this._onDragEnd.event;
            this.addDisposables(this._onDragStart, this._onDragMove, this._onDragEnd);
        }
        get active() {
            return this._active;
        }
        registerTarget(target) {
            this._targets.add(target);
            this._targetByElement.set(target.element, target);
            return {
                dispose: () => {
                    this._targets.delete(target);
                    if (this._targetByElement.get(target.element) === target) {
                        this._targetByElement.delete(target.element);
                    }
                    if (this._currentTarget === target) {
                        this._currentTarget = undefined;
                    }
                },
            };
        }
        beginDrag(args) {
            var _a, _b, _c;
            if (this._active) {
                this.cancel();
            }
            const { pointerEvent, source } = args;
            // Call `getData()` before mutating controller state — a throw
            // here would otherwise leave `_active` populated with no window
            // listeners installed, blocking every subsequent drag.
            const dataDisposable = args.getData();
            this._active = {
                pointerId: pointerEvent.pointerId,
                startX: pointerEvent.clientX,
                startY: pointerEvent.clientY,
                source,
            };
            this._onDragMoveCallback = args.onDragMove;
            this._onDragEndCallback = args.onDragEnd;
            this._dataDisposable = dataDisposable;
            this._ghost = args.ghost;
            // Iframes capture pointermove once the cursor crosses into them,
            // which would freeze the drag from the parent window's POV.
            this._iframeShield = disableIframePointEvents((_a = source.ownerDocument) !== null && _a !== void 0 ? _a : document);
            const startEvent = {
                clientX: pointerEvent.clientX,
                clientY: pointerEvent.clientY,
                pointerEvent,
            };
            this._onDragStart.fire(startEvent);
            // Source's owning window — popout drags fire on their own window,
            // not the main one.
            const targetWindow = (_c = (_b = source.ownerDocument) === null || _b === void 0 ? void 0 : _b.defaultView) !== null && _c !== void 0 ? _c : window;
            this._moveListener = addDisposableListener(targetWindow, 'pointermove', (e) => {
                if (!this._active || e.pointerId !== this._active.pointerId) {
                    return;
                }
                this._handleMove(e);
            });
            this._upListener = addDisposableListener(targetWindow, 'pointerup', (e) => {
                if (!this._active || e.pointerId !== this._active.pointerId) {
                    return;
                }
                this._handleEnd(e, true);
            });
            this._cancelListener = addDisposableListener(targetWindow, 'pointercancel', (e) => {
                if (!this._active || e.pointerId !== this._active.pointerId) {
                    return;
                }
                this._handleEnd(e, false);
            });
        }
        cancel() {
            var _a, _b;
            if (!this._active) {
                return;
            }
            (_a = this._currentTarget) === null || _a === void 0 ? void 0 : _a.handleDragLeave();
            this._teardown();
            (_b = this._dataDisposable) === null || _b === void 0 ? void 0 : _b.dispose();
            this._dataDisposable = undefined;
        }
        _findTargetUnder(x, y) {
            var _a, _b;
            // `elementsFromPoint` is topmost-first; walk up to find the closest
            // registered ancestor (so a tab beats the layout-root that contains it).
            // Use the source's owning document so popout drags hit their own targets.
            const sourceDoc = (_b = (_a = this._active) === null || _a === void 0 ? void 0 : _a.source.ownerDocument) !== null && _b !== void 0 ? _b : document;
            const elements = sourceDoc.elementsFromPoint(x, y);
            for (const el of elements) {
                let current = el;
                while (current) {
                    const target = this._targetByElement.get(current);
                    if (target) {
                        return target;
                    }
                    current = current.parentElement;
                }
            }
            return undefined;
        }
        _handleMove(e) {
            var _a, _b, _c;
            (_a = this._ghost) === null || _a === void 0 ? void 0 : _a.update(e.clientX, e.clientY);
            const dragEvent = {
                clientX: e.clientX,
                clientY: e.clientY,
                pointerEvent: e,
            };
            const newTarget = this._findTargetUnder(e.clientX, e.clientY);
            if (newTarget !== this._currentTarget) {
                (_b = this._currentTarget) === null || _b === void 0 ? void 0 : _b.handleDragLeave();
                this._currentTarget = newTarget;
            }
            if (newTarget) {
                newTarget.handleDragOver(dragEvent);
            }
            (_c = this._onDragMoveCallback) === null || _c === void 0 ? void 0 : _c.call(this, dragEvent);
            this._onDragMove.fire(dragEvent);
        }
        _handleEnd(e, dropped) {
            var _a;
            const dragEvent = {
                clientX: e.clientX,
                clientY: e.clientY,
                pointerEvent: e,
            };
            if (dropped && this._currentTarget) {
                this._currentTarget.handleDrop(dragEvent);
            }
            else {
                (_a = this._currentTarget) === null || _a === void 0 ? void 0 : _a.handleDragLeave();
            }
            const onEnd = this._onDragEndCallback;
            const dataDisposable = this._dataDisposable;
            this._teardown();
            this._dataDisposable = undefined;
            // Defer disposal so drop handlers can still read the transfer data.
            setTimeout(() => dataDisposable === null || dataDisposable === void 0 ? void 0 : dataDisposable.dispose(), 0);
            onEnd === null || onEnd === void 0 ? void 0 : onEnd(dragEvent, dropped);
            this._onDragEnd.fire(dragEvent);
        }
        _teardown() {
            var _a, _b, _c, _d, _e;
            this._currentTarget = undefined;
            this._active = undefined;
            this._onDragMoveCallback = undefined;
            this._onDragEndCallback = undefined;
            (_a = this._ghost) === null || _a === void 0 ? void 0 : _a.dispose();
            this._ghost = undefined;
            (_b = this._iframeShield) === null || _b === void 0 ? void 0 : _b.release();
            this._iframeShield = undefined;
            (_c = this._moveListener) === null || _c === void 0 ? void 0 : _c.dispose();
            (_d = this._upListener) === null || _d === void 0 ? void 0 : _d.dispose();
            (_e = this._cancelListener) === null || _e === void 0 ? void 0 : _e.dispose();
            this._moveListener = undefined;
            this._upListener = undefined;
            this._cancelListener = undefined;
        }
    }

    const DEFAULT_ACTIVATION_SIZE = {
        value: 20,
        type: 'percentage',
    };
    /** Pointer-driven counterpart to `Droptarget` with identical visual output. */
    class PointerDropTarget extends CompositeDisposable {
        get disabled() {
            return this._disabled;
        }
        set disabled(value) {
            this._disabled = value;
            if (value) {
                this._removeOverlay();
            }
        }
        get state() {
            return this._state;
        }
        constructor(element, options) {
            super();
            this.element = element;
            this.options = options;
            this._onDrop = new Emitter();
            this.onDrop = this._onDrop.event;
            this._onWillShowOverlay = new Emitter();
            this.onWillShowOverlay = this._onWillShowOverlay.event;
            this._disabled = false;
            this._acceptedTargetZonesSet = new Set(options.acceptedTargetZones);
            const handle = {
                element: this.element,
                handleDragOver: (e) => this._onDragOver(e),
                handleDragLeave: () => this._onDragLeave(),
                handleDrop: (e) => this._onDropEvent(e),
            };
            this.addDisposables(this._onDrop, this._onWillShowOverlay, PointerDragController.getInstance().registerTarget(handle));
        }
        setTargetZones(zones) {
            this._acceptedTargetZonesSet = new Set(zones);
        }
        setOverlayModel(model) {
            this.options.overlayModel = model;
        }
        dispose() {
            this._removeOverlay();
            super.dispose();
        }
        _onDragOver(event) {
            var _a, _b, _c, _d, _e;
            if (this._disabled) {
                this._removeOverlay();
                return;
            }
            const overrideTarget = (_b = (_a = this.options).getOverrideTarget) === null || _b === void 0 ? void 0 : _b.call(_a);
            if (this._acceptedTargetZonesSet.size === 0) {
                if (overrideTarget) {
                    return;
                }
                this._removeOverlay();
                return;
            }
            const outlineEl = (_e = (_d = (_c = this.options).getOverlayOutline) === null || _d === void 0 ? void 0 : _d.call(_c)) !== null && _e !== void 0 ? _e : this.element;
            const width = outlineEl.offsetWidth;
            const height = outlineEl.offsetHeight;
            if (width === 0 || height === 0) {
                return;
            }
            const rect = outlineEl.getBoundingClientRect();
            const x = event.clientX - rect.left;
            const y = event.clientY - rect.top;
            const quadrant = this._calculateQuadrant(x, y, width, height);
            if (quadrant === null) {
                this._removeOverlay();
                return;
            }
            if (!this.options.canDisplayOverlay(event.pointerEvent, quadrant)) {
                if (overrideTarget) {
                    return;
                }
                this._removeOverlay();
                return;
            }
            const willShow = new WillShowOverlayEvent({
                nativeEvent: event.pointerEvent,
                position: quadrant,
            });
            this._onWillShowOverlay.fire(willShow);
            if (willShow.defaultPrevented) {
                this._removeOverlay();
                return;
            }
            if (overrideTarget) {
                renderAnchoredOverlay({
                    outlineElement: outlineEl,
                    targetModel: overrideTarget,
                    quadrant,
                    width,
                    height,
                    overlayModel: this.options.overlayModel,
                    className: this.options.className,
                });
                this._state = quadrant;
                return;
            }
            if (!this._targetElement) {
                const els = createOverlayElements();
                this._targetElement = els.dropzone;
                this._overlayElement = els.selection;
                this._state = 'center';
                this.element.classList.add('dv-drop-target');
                this.element.append(this._targetElement);
            }
            if (this._overlayElement) {
                renderInPlaceOverlay(this._overlayElement, quadrant, width, height, this.options.overlayModel);
            }
            this._state = quadrant;
        }
        _onDragLeave() {
            var _a, _b;
            const overrideTarget = (_b = (_a = this.options).getOverrideTarget) === null || _b === void 0 ? void 0 : _b.call(_a);
            // Anchor target owns its own lifecycle; just clear our latched
            // state so a subsequent pointerup doesn't fire a stale drop.
            if (overrideTarget) {
                this._state = undefined;
                overrideTarget.clear();
                return;
            }
            this._removeOverlay();
        }
        _onDropEvent(event) {
            var _a, _b;
            const state = this._state;
            const overrideTarget = (_b = (_a = this.options).getOverrideTarget) === null || _b === void 0 ? void 0 : _b.call(_a);
            this._removeOverlay();
            overrideTarget === null || overrideTarget === void 0 ? void 0 : overrideTarget.clear();
            if (state) {
                this._onDrop.fire({
                    position: state,
                    nativeEvent: event.pointerEvent,
                });
            }
        }
        _calculateQuadrant(x, y, width, height) {
            var _a, _b;
            const activation = (_b = (_a = this.options.overlayModel) === null || _a === void 0 ? void 0 : _a.activationSize) !== null && _b !== void 0 ? _b : DEFAULT_ACTIVATION_SIZE;
            if (activation.type === 'percentage') {
                return calculateQuadrantAsPercentage(this._acceptedTargetZonesSet, x, y, width, height, activation.value);
            }
            return calculateQuadrantAsPixels(this._acceptedTargetZonesSet, x, y, width, height, activation.value);
        }
        _removeOverlay() {
            var _a;
            if (this._targetElement) {
                this._state = undefined;
                (_a = this._targetElement.parentElement) === null || _a === void 0 ? void 0 : _a.classList.remove('dv-drop-target');
                this._targetElement.remove();
                this._targetElement = undefined;
                this._overlayElement = undefined;
            }
            else {
                this._state = undefined;
            }
        }
    }

    const DEFAULT_THRESHOLD = 5;
    const DEFAULT_TOUCH_INITIATION_DELAY = 250;
    const DEFAULT_PRESS_TOLERANCE = 8;
    /**
     * Pointer-event drag source. Waits for movement past `threshold` (and
     * touch-only `touchInitiationDelay`) before promoting to a drag so taps
     * pass through unaffected.
     */
    class PointerDragSource extends CompositeDisposable {
        constructor(element, options) {
            var _a;
            super();
            this.element = element;
            this.options = options;
            this._disabled = false;
            this._armed = false;
            this._startX = 0;
            this._startY = 0;
            this._touchOnly = (_a = options.touchOnly) !== null && _a !== void 0 ? _a : true;
            this.addDisposables(addDisposableListener(this.element, 'pointerdown', (e) => {
                this._onPointerDown(e);
            }));
        }
        setDisabled(value) {
            this._disabled = value;
            if (value) {
                this._cancelPending();
            }
        }
        /**
         * `false` lets the pointer source also handle mouse pointers; used when
         * `dndStrategy: 'pointer'` to drive every input type through this path.
         */
        setTouchOnly(value) {
            if (this._touchOnly === value) {
                return;
            }
            this._touchOnly = value;
            // A pending mouse-tracked drag should be abandoned if we re-enable
            // the touch-only filter mid-flight.
            if (value) {
                this._cancelPending();
            }
        }
        _shouldHandle(event) {
            var _a, _b;
            if (this._disabled) {
                return false;
            }
            // Pointer-type filter runs before isCancelled — consumer state read
            // by isCancelled may not be populated for events we'll never handle.
            if (this._touchOnly &&
                event.pointerType !== 'touch' &&
                event.pointerType !== 'pen') {
                return false;
            }
            if ((_b = (_a = this.options).isCancelled) === null || _b === void 0 ? void 0 : _b.call(_a, event)) {
                return false;
            }
            return true;
        }
        _onPointerDown(event) {
            var _a, _b, _c, _d, _e;
            if (!this._shouldHandle(event)) {
                return;
            }
            // Defensive: a fresh pointerdown supersedes any in-flight tracking.
            this._cancelPending();
            this._pendingPointerId = event.pointerId;
            this._startX = event.clientX;
            this._startY = event.clientY;
            this._startEvent = event;
            const isTouch = event.pointerType === 'touch' || event.pointerType === 'pen';
            // Touch waits a short window so a still finger can press-and-hold
            // before drifting; once the timer fires, any motion past `threshold`
            // begins the drag.
            const initiationDelayOpt = this.options.touchInitiationDelay;
            const initiationDelay = (_a = (typeof initiationDelayOpt === 'function'
                ? initiationDelayOpt()
                : initiationDelayOpt)) !== null && _a !== void 0 ? _a : DEFAULT_TOUCH_INITIATION_DELAY;
            this._armed = !isTouch || initiationDelay <= 0;
            if (isTouch && initiationDelay > 0 && isFinite(initiationDelay)) {
                this._armTimer = setTimeout(() => {
                    this._armTimer = undefined;
                    this._armed = true;
                }, initiationDelay);
            }
            const threshold = (_b = this.options.threshold) !== null && _b !== void 0 ? _b : DEFAULT_THRESHOLD;
            const pressToleranceOpt = this.options.pressTolerance;
            const pressTolerance = (_c = (typeof pressToleranceOpt === 'function'
                ? pressToleranceOpt()
                : pressToleranceOpt)) !== null && _c !== void 0 ? _c : DEFAULT_PRESS_TOLERANCE;
            // Source's owning window — popout drags fire on their own window.
            const targetWindow = (_e = (_d = this.element.ownerDocument) === null || _d === void 0 ? void 0 : _d.defaultView) !== null && _e !== void 0 ? _e : window;
            this._pendingMoveListener = addDisposableListener(targetWindow, 'pointermove', (moveEvent) => {
                if (moveEvent.pointerId !== this._pendingPointerId) {
                    return;
                }
                const dx = moveEvent.clientX - this._startX;
                const dy = moveEvent.clientY - this._startY;
                const distance = Math.hypot(dx, dy);
                if (this._armed) {
                    if (distance >= threshold) {
                        this._beginDrag(moveEvent);
                    }
                    return;
                }
                // Pre-arm phase: a flick past `pressTolerance` in any
                // direction is treated as drag intent. The element opts out
                // of native scroll via `touch-action: none`; container-level
                // scrolling lives on the surrounding strip's empty space.
                if (distance > pressTolerance) {
                    this._beginDrag(moveEvent);
                }
            });
            this._pendingUpListener = addDisposableListener(targetWindow, 'pointerup', (upEvent) => {
                if (upEvent.pointerId !== this._pendingPointerId) {
                    return;
                }
                this._cancelPending();
            });
            this._pendingCancelListener = addDisposableListener(targetWindow, 'pointercancel', (cancelEvent) => {
                if (cancelEvent.pointerId !== this._pendingPointerId) {
                    return;
                }
                this._cancelPending();
            });
        }
        /** For sibling gesture detectors (e.g. LongPressDetector) to dismiss a pending drag. */
        cancelPending() {
            this._cancelPending();
        }
        _cancelPending() {
            var _a, _b, _c;
            this._pendingPointerId = undefined;
            if (this._armTimer !== undefined) {
                clearTimeout(this._armTimer);
                this._armTimer = undefined;
            }
            this._armed = false;
            (_a = this._pendingMoveListener) === null || _a === void 0 ? void 0 : _a.dispose();
            (_b = this._pendingUpListener) === null || _b === void 0 ? void 0 : _b.dispose();
            (_c = this._pendingCancelListener) === null || _c === void 0 ? void 0 : _c.dispose();
            this._pendingMoveListener = undefined;
            this._pendingUpListener = undefined;
            this._pendingCancelListener = undefined;
            this._startEvent = undefined;
        }
        _beginDrag(triggerEvent) {
            var _a, _b, _c, _d, _e;
            const startEvent = (_a = this._startEvent) !== null && _a !== void 0 ? _a : triggerEvent;
            this._cancelPending();
            (_c = (_b = this.options).onDragStart) === null || _c === void 0 ? void 0 : _c.call(_b, startEvent);
            const ghost = (_e = (_d = this.options).createGhost) === null || _e === void 0 ? void 0 : _e.call(_d, startEvent);
            PointerDragController.getInstance().beginDrag({
                pointerEvent: triggerEvent,
                source: this.element,
                getData: () => this.options.getData(startEvent),
                ghost,
                onDragMove: this.options.onDragMove,
                onDragEnd: this.options.onDragEnd,
            });
        }
        dispose() {
            this._cancelPending();
            super.dispose();
        }
    }

    /**
     * Floating clone that follows the pointer; appended to the owning
     * document's body with `pointer-events: none` so it doesn't intercept
     * hit-testing.
     */
    class PointerGhost {
        constructor(opts) {
            var _a, _b, _c, _d, _e;
            this._disposed = false;
            this.element = opts.element;
            this.offsetX = (_a = opts.offsetX) !== null && _a !== void 0 ? _a : 0;
            this.offsetY = (_b = opts.offsetY) !== null && _b !== void 0 ? _b : 0;
            // Animate via transform (see update); position:fixed for scroll-independence.
            this.element.style.position = 'fixed';
            this.element.style.left = '0px';
            this.element.style.top = '0px';
            this.element.style.pointerEvents = 'none';
            this.element.style.zIndex = '99999';
            this.element.style.opacity = String((_c = opts.opacity) !== null && _c !== void 0 ? _c : 0.8);
            this.element.style.willChange = 'transform';
            this.element.style.transform = `translate3d(${opts.initialX - this.offsetX}px, ${opts.initialY - this.offsetY}px, 0)`;
            const ownerDocument = (_e = (_d = opts.owner) === null || _d === void 0 ? void 0 : _d.ownerDocument) !== null && _e !== void 0 ? _e : document;
            ownerDocument.body.appendChild(this.element);
        }
        update(clientX, clientY) {
            if (this._disposed) {
                return;
            }
            // translate3d composites on the GPU — no layout per pointermove.
            this.element.style.transform = `translate3d(${clientX - this.offsetX}px, ${clientY - this.offsetY}px, 0)`;
        }
        dispose() {
            if (this._disposed) {
                return;
            }
            this._disposed = true;
            this.element.remove();
        }
    }

    /**
     * HTML5 drag source. Listens for the native `dragstart` event, calls
     * `getData` to populate transfer, optionally renders the ghost via
     * `setDragImage`, fires `onDragStart` / `onDragEnd`, and tears down the
     * transfer disposer after `dragend`.
     */
    class Html5DragSource extends CompositeDisposable {
        constructor(el, opts) {
            super();
            this.el = el;
            this.opts = opts;
            this._dataDisposable = new MutableDisposable();
            this._pointerEventsDisposable = new MutableDisposable();
            this._disabled = !!opts.disabled;
            this.addDisposables(this._dataDisposable, this._pointerEventsDisposable, addDisposableListener(this.el, 'dragstart', (event) => {
                var _a, _b, _c, _d, _e, _f, _g, _h, _j;
                if (event.defaultPrevented ||
                    this._disabled ||
                    ((_b = (_a = this.opts).isCancelled) === null || _b === void 0 ? void 0 : _b.call(_a, event))) {
                    event.preventDefault();
                    return;
                }
                // Iframes capture pointermove once the cursor enters them,
                // which freezes drag tracking from the parent window's
                // POV. Shield the source's owning document so popout-window
                // drags shield the popout, not the main window.
                const iframes = disableIframePointEvents((_c = this.el.ownerDocument) !== null && _c !== void 0 ? _c : document);
                this._pointerEventsDisposable.value = {
                    dispose: () => iframes.release(),
                };
                this.el.classList.add('dv-dragged');
                setTimeout(() => this.el.classList.remove('dv-dragged'), 0);
                this._dataDisposable.value = this.opts.getData(event);
                const ghost = (_e = (_d = this.opts).createGhost) === null || _e === void 0 ? void 0 : _e.call(_d, event);
                if (ghost && event.dataTransfer) {
                    addGhostImage(event.dataTransfer, ghost.element, {
                        x: (_f = ghost.offsetX) !== null && _f !== void 0 ? _f : 0,
                        y: (_g = ghost.offsetY) !== null && _g !== void 0 ? _g : 0,
                    });
                    if (ghost.dispose) {
                        // addGhostImage removes the element from the DOM on
                        // the next tick; dispose the framework renderer on
                        // the same schedule.
                        const disposeGhost = ghost.dispose;
                        setTimeout(() => disposeGhost(), 0);
                    }
                }
                if (event.dataTransfer) {
                    event.dataTransfer.effectAllowed = 'move';
                    // Some third-party DnD libs (e.g. react-dnd) cancel the
                    // dragstart when `dataTransfer.types` is empty.
                    if (event.dataTransfer.items.length === 0) {
                        event.dataTransfer.setData('text/plain', '');
                    }
                }
                (_j = (_h = this.opts).onDragStart) === null || _j === void 0 ? void 0 : _j.call(_h, event);
            }), addDisposableListener(this.el, 'dragend', (event) => {
                var _a, _b;
                this._pointerEventsDisposable.dispose();
                // Defer disposal so drop handlers can still read the
                // transfer payload before it clears.
                setTimeout(() => this._dataDisposable.dispose(), 0);
                (_b = (_a = this.opts).onDragEnd) === null || _b === void 0 ? void 0 : _b.call(_a, event);
            }));
        }
        setDisabled(value) {
            this._disabled = value;
        }
        setTouchOnly(_) {
            // No-op — HTML5 path can't filter by pointer type.
        }
        cancelPending() {
            // No-op — HTML5 has no pre-arm phase to cancel.
        }
    }
    class Html5DragBackend {
        constructor() {
            this.kind = 'html5';
        }
        createDropTarget(element, options) {
            return new Droptarget(element, options);
        }
        createDragSource(element, options) {
            return new Html5DragSource(element, options);
        }
    }
    class PointerDragBackend {
        constructor() {
            this.kind = 'pointer';
        }
        createDropTarget(element, options) {
            return new PointerDropTarget(element, options);
        }
        createDragSource(element, options) {
            const pointerCreateGhost = options.createGhost
                ? (event) => {
                    const spec = options.createGhost(event);
                    if (!spec) {
                        return undefined;
                    }
                    const ghost = new PointerGhost({
                        element: spec.element,
                        initialX: event.clientX,
                        initialY: event.clientY,
                        offsetX: spec.offsetX,
                        offsetY: spec.offsetY,
                        owner: element,
                    });
                    if (spec.dispose) {
                        const baseDispose = ghost.dispose.bind(ghost);
                        const disposeSpec = spec.dispose;
                        ghost.dispose = () => {
                            baseDispose();
                            disposeSpec();
                        };
                    }
                    return ghost;
                }
                : undefined;
            const source = new PointerDragSource(element, {
                getData: options.getData,
                isCancelled: options.isCancelled,
                onDragStart: options.onDragStart,
                onDragEnd: options.onDragEnd
                    ? (event) => options.onDragEnd(event.pointerEvent)
                    : undefined,
                createGhost: pointerCreateGhost,
                touchOnly: options.touchOnly,
                touchInitiationDelay: options.touchInitiationDelay,
                pressTolerance: options.pressTolerance,
                threshold: options.threshold,
            });
            if (options.disabled) {
                source.setDisabled(true);
            }
            return source;
        }
    }
    const html5Backend = new Html5DragBackend();
    const pointerBackend = new PointerDragBackend();

    const PROPERTY_KEYS_PANEVIEW = (() => {
        /**
         * by readong the keys from an empty value object TypeScript will error
         * when we add or remove new properties to `DockviewOptions`
         */
        const properties = {
            disableAutoResizing: undefined,
            disableDnd: undefined,
            className: undefined,
        };
        return Object.keys(properties);
    })();
    class PaneviewUnhandledDragOverEvent extends AcceptableEvent {
        constructor(nativeEvent, position, getData, panel) {
            super();
            this.nativeEvent = nativeEvent;
            this.position = position;
            this.getData = getData;
            this.panel = panel;
        }
    }

    class WillFocusEvent extends DockviewEvent {
        constructor() {
            super();
        }
    }
    /**
     * A core api implementation that should be used across all panel-like objects
     */
    class PanelApiImpl extends CompositeDisposable {
        get isFocused() {
            return this._isFocused;
        }
        get isActive() {
            return this._isActive;
        }
        get isVisible() {
            return this._isVisible;
        }
        get width() {
            return this._width;
        }
        get height() {
            return this._height;
        }
        constructor(id, component) {
            super();
            this.id = id;
            this.component = component;
            this._isFocused = false;
            this._isActive = false;
            this._isVisible = true;
            this._width = 0;
            this._height = 0;
            this._parameters = {};
            this.panelUpdatesDisposable = new MutableDisposable();
            this._onDidDimensionChange = new Emitter();
            this.onDidDimensionsChange = this._onDidDimensionChange.event;
            this._onDidChangeFocus = new Emitter();
            this.onDidFocusChange = this._onDidChangeFocus.event;
            //
            this._onWillFocus = new Emitter();
            this.onWillFocus = this._onWillFocus.event;
            //
            this._onDidVisibilityChange = new Emitter();
            this.onDidVisibilityChange = this._onDidVisibilityChange.event;
            this._onWillVisibilityChange = new Emitter();
            this.onWillVisibilityChange = this._onWillVisibilityChange.event;
            this._onDidActiveChange = new Emitter();
            this.onDidActiveChange = this._onDidActiveChange.event;
            this._onActiveChange = new Emitter();
            this.onActiveChange = this._onActiveChange.event;
            this._onDidParametersChange = new Emitter();
            this.onDidParametersChange = this._onDidParametersChange.event;
            this.addDisposables(this.onDidFocusChange((event) => {
                this._isFocused = event.isFocused;
            }), this.onDidActiveChange((event) => {
                this._isActive = event.isActive;
            }), this.onDidVisibilityChange((event) => {
                this._isVisible = event.isVisible;
            }), this.onDidDimensionsChange((event) => {
                this._width = event.width;
                this._height = event.height;
            }), this.panelUpdatesDisposable, this._onDidDimensionChange, this._onDidChangeFocus, this._onDidVisibilityChange, this._onDidActiveChange, this._onWillFocus, this._onActiveChange, this._onWillFocus, this._onWillVisibilityChange, this._onDidParametersChange);
        }
        getParameters() {
            return this._parameters;
        }
        initialize(panel) {
            this.panelUpdatesDisposable.value = this._onDidParametersChange.event((parameters) => {
                this._parameters = parameters;
                panel.update({
                    params: parameters,
                });
            });
        }
        setVisible(isVisible) {
            this._onWillVisibilityChange.fire({ isVisible });
        }
        setActive() {
            this._onActiveChange.fire();
        }
        updateParameters(parameters) {
            this._onDidParametersChange.fire(parameters);
        }
    }

    class SplitviewPanelApiImpl extends PanelApiImpl {
        //
        constructor(id, component) {
            super(id, component);
            this._onDidConstraintsChangeInternal = new Emitter();
            this.onDidConstraintsChangeInternal = this._onDidConstraintsChangeInternal.event;
            //
            this._onDidConstraintsChange = new Emitter({
                replay: true,
            });
            this.onDidConstraintsChange = this._onDidConstraintsChange.event;
            //
            this._onDidSizeChange = new Emitter();
            this.onDidSizeChange = this._onDidSizeChange.event;
            this.addDisposables(this._onDidConstraintsChangeInternal, this._onDidConstraintsChange, this._onDidSizeChange);
        }
        setConstraints(value) {
            this._onDidConstraintsChangeInternal.fire(value);
        }
        setSize(event) {
            this._onDidSizeChange.fire(event);
        }
    }

    class PaneviewPanelApiImpl extends SplitviewPanelApiImpl {
        set pane(pane) {
            this._pane = pane;
        }
        constructor(id, component) {
            super(id, component);
            this._onDidExpansionChange = new Emitter({
                replay: true,
            });
            this.onDidExpansionChange = this._onDidExpansionChange.event;
            this._onMouseEnter = new Emitter({});
            this.onMouseEnter = this._onMouseEnter.event;
            this._onMouseLeave = new Emitter({});
            this.onMouseLeave = this._onMouseLeave.event;
            this.addDisposables(this._onDidExpansionChange, this._onMouseEnter, this._onMouseLeave);
        }
        setExpanded(isExpanded) {
            var _a;
            (_a = this._pane) === null || _a === void 0 ? void 0 : _a.setExpanded(isExpanded);
        }
        get isExpanded() {
            var _a;
            return !!((_a = this._pane) === null || _a === void 0 ? void 0 : _a.isExpanded());
        }
    }

    class BasePanelView extends CompositeDisposable {
        get element() {
            return this._element;
        }
        get width() {
            return this._width;
        }
        get height() {
            return this._height;
        }
        get params() {
            var _a;
            return (_a = this._params) === null || _a === void 0 ? void 0 : _a.params;
        }
        constructor(id, component, api) {
            super();
            this.id = id;
            this.component = component;
            this.api = api;
            this._height = 0;
            this._width = 0;
            this._element = document.createElement('div');
            this._element.tabIndex = -1;
            this._element.style.outline = 'none';
            this._element.style.height = '100%';
            this._element.style.width = '100%';
            this._element.style.overflow = 'hidden';
            const focusTracker = trackFocus(this._element);
            this.addDisposables(this.api, focusTracker.onDidFocus(() => {
                this.api._onDidChangeFocus.fire({ isFocused: true });
            }), focusTracker.onDidBlur(() => {
                this.api._onDidChangeFocus.fire({ isFocused: false });
            }), focusTracker);
        }
        focus() {
            const event = new WillFocusEvent();
            this.api._onWillFocus.fire(event);
            if (event.defaultPrevented) {
                return;
            }
            this._element.focus();
        }
        layout(width, height) {
            this._width = width;
            this._height = height;
            this.api._onDidDimensionChange.fire({ width, height });
            if (this.part) {
                if (this._params) {
                    this.part.update(this._params.params);
                }
            }
        }
        init(parameters) {
            this._params = parameters;
            this.part = this.getComponent();
        }
        update(event) {
            var _a, _b;
            // merge the new parameters with the existing parameters
            this._params = Object.assign(Object.assign({}, this._params), { params: Object.assign(Object.assign({}, (_a = this._params) === null || _a === void 0 ? void 0 : _a.params), event.params) });
            /**
             * delete new keys that have a value of undefined,
             * allow values of null
             */
            for (const key of Object.keys(event.params)) {
                if (event.params[key] === undefined) {
                    delete this._params.params[key];
                }
            }
            // update the view with the updated props
            (_b = this.part) === null || _b === void 0 ? void 0 : _b.update({ params: this._params.params });
        }
        toJSON() {
            var _a, _b;
            const params = (_b = (_a = this._params) === null || _a === void 0 ? void 0 : _a.params) !== null && _b !== void 0 ? _b : {};
            return {
                id: this.id,
                component: this.component,
                params: Object.keys(params).length > 0 ? params : undefined,
            };
        }
        dispose() {
            var _a;
            this.api.dispose();
            (_a = this.part) === null || _a === void 0 ? void 0 : _a.dispose();
            super.dispose();
        }
    }

    class PaneviewPanel extends BasePanelView {
        set orientation(value) {
            this._orientation = value;
        }
        get orientation() {
            return this._orientation;
        }
        get minimumSize() {
            const headerSize = this.headerSize;
            const expanded = this.isExpanded();
            const minimumBodySize = expanded ? this._minimumBodySize : 0;
            return headerSize + minimumBodySize;
        }
        get maximumSize() {
            const headerSize = this.headerSize;
            const expanded = this.isExpanded();
            const maximumBodySize = expanded ? this._maximumBodySize : 0;
            return headerSize + maximumBodySize;
        }
        get size() {
            return this._size;
        }
        get orthogonalSize() {
            return this._orthogonalSize;
        }
        set orthogonalSize(size) {
            this._orthogonalSize = size;
        }
        get minimumBodySize() {
            return this._minimumBodySize;
        }
        set minimumBodySize(value) {
            this._minimumBodySize = typeof value === 'number' ? value : 0;
        }
        get maximumBodySize() {
            return this._maximumBodySize;
        }
        set maximumBodySize(value) {
            this._maximumBodySize =
                typeof value === 'number' ? value : Number.POSITIVE_INFINITY;
        }
        get headerVisible() {
            return this._headerVisible;
        }
        set headerVisible(value) {
            this._headerVisible = value;
            this.header.style.display = value ? '' : 'none';
        }
        constructor(options) {
            super(options.id, options.component, new PaneviewPanelApiImpl(options.id, options.component));
            this._onDidChangeExpansionState = new Emitter({ replay: true });
            this.onDidChangeExpansionState = this._onDidChangeExpansionState.event;
            this._onDidChange = new Emitter();
            this.onDidChange = this._onDidChange.event;
            this._orthogonalSize = 0;
            this._size = 0;
            this._isExpanded = false;
            this.api.pane = this; // TODO cannot use 'this' before 'super'
            this.api.initialize(this);
            this.headerSize = options.headerSize;
            this.headerComponent = options.headerComponent;
            this._minimumBodySize = options.minimumBodySize;
            this._maximumBodySize = options.maximumBodySize;
            this._isExpanded = options.isExpanded;
            this._headerVisible = options.isHeaderVisible;
            this._onDidChangeExpansionState.fire(this.isExpanded()); // initialize value
            this._orientation = options.orientation;
            this.element.classList.add('dv-pane');
            this.addDisposables(this.api.onWillVisibilityChange((event) => {
                const { isVisible } = event;
                const { accessor } = this._params;
                accessor.setVisible(this, isVisible);
            }), this.api.onDidSizeChange((event) => {
                this._onDidChange.fire({ size: event.size });
            }), addDisposableListener(this.element, 'mouseenter', (ev) => {
                this.api._onMouseEnter.fire(ev);
            }), addDisposableListener(this.element, 'mouseleave', (ev) => {
                this.api._onMouseLeave.fire(ev);
            }));
            this.addDisposables(this._onDidChangeExpansionState, this.onDidChangeExpansionState((isPanelExpanded) => {
                this.api._onDidExpansionChange.fire({
                    isExpanded: isPanelExpanded,
                });
            }), this.api.onDidFocusChange((e) => {
                if (!this.header) {
                    return;
                }
                if (e.isFocused) {
                    addClasses(this.header, 'focused');
                }
                else {
                    removeClasses(this.header, 'focused');
                }
            }));
            this.renderOnce();
        }
        setVisible(isVisible) {
            this.api._onDidVisibilityChange.fire({ isVisible });
        }
        setActive(isActive) {
            this.api._onDidActiveChange.fire({ isActive });
        }
        isExpanded() {
            return this._isExpanded;
        }
        setExpanded(expanded) {
            if (this._isExpanded === expanded) {
                return;
            }
            this._isExpanded = expanded;
            if (expanded) {
                if (this.animationTimer) {
                    clearTimeout(this.animationTimer);
                }
                if (this.body) {
                    this.element.appendChild(this.body);
                }
            }
            else {
                this.animationTimer = setTimeout(() => {
                    var _a;
                    (_a = this.body) === null || _a === void 0 ? void 0 : _a.remove();
                }, 200);
            }
            this._onDidChange.fire(expanded ? { size: this.width } : {});
            this._onDidChangeExpansionState.fire(expanded);
        }
        layout(size, orthogonalSize) {
            this._size = size;
            this._orthogonalSize = orthogonalSize;
            const [width, height] = this.orientation === exports.Orientation.HORIZONTAL
                ? [size, orthogonalSize]
                : [orthogonalSize, size];
            super.layout(width, height);
        }
        init(parameters) {
            var _a, _b;
            super.init(parameters);
            if (typeof parameters.minimumBodySize === 'number') {
                this.minimumBodySize = parameters.minimumBodySize;
            }
            if (typeof parameters.maximumBodySize === 'number') {
                this.maximumBodySize = parameters.maximumBodySize;
            }
            this.bodyPart = this.getBodyComponent();
            this.headerPart = this.getHeaderComponent();
            this.bodyPart.init(Object.assign(Object.assign({}, parameters), { api: this.api }));
            this.headerPart.init(Object.assign(Object.assign({}, parameters), { api: this.api }));
            (_a = this.body) === null || _a === void 0 ? void 0 : _a.append(this.bodyPart.element);
            (_b = this.header) === null || _b === void 0 ? void 0 : _b.append(this.headerPart.element);
            if (typeof parameters.isExpanded === 'boolean') {
                this.setExpanded(parameters.isExpanded);
            }
        }
        toJSON() {
            const params = this._params;
            return Object.assign(Object.assign({}, super.toJSON()), { headerComponent: this.headerComponent, title: params.title });
        }
        renderOnce() {
            this.header = document.createElement('div');
            this.header.tabIndex = 0;
            this.header.className = 'dv-pane-header';
            this.header.style.height = `${this.headerSize}px`;
            this.header.style.lineHeight = `${this.headerSize}px`;
            this.header.style.minHeight = `${this.headerSize}px`;
            this.header.style.maxHeight = `${this.headerSize}px`;
            this.element.appendChild(this.header);
            this.body = document.createElement('div');
            this.body.className = 'dv-pane-body';
            this.element.appendChild(this.body);
        }
        // TODO slightly hacky by-pass of the component to create a body and header component
        getComponent() {
            return {
                update: (params) => {
                    var _a, _b;
                    (_a = this.bodyPart) === null || _a === void 0 ? void 0 : _a.update({ params });
                    (_b = this.headerPart) === null || _b === void 0 ? void 0 : _b.update({ params });
                },
                dispose: () => {
                    var _a, _b;
                    (_a = this.bodyPart) === null || _a === void 0 ? void 0 : _a.dispose();
                    (_b = this.headerPart) === null || _b === void 0 ? void 0 : _b.dispose();
                },
            };
        }
    }

    class DraggablePaneviewPanel extends PaneviewPanel {
        constructor(options) {
            super({
                id: options.id,
                component: options.component,
                headerComponent: options.headerComponent,
                orientation: options.orientation,
                isExpanded: options.isExpanded,
                isHeaderVisible: true,
                headerSize: options.headerSize,
                minimumBodySize: options.minimumBodySize,
                maximumBodySize: options.maximumBodySize,
            });
            this._onDidDrop = new Emitter();
            this.onDidDrop = this._onDidDrop.event;
            this._onUnhandledDragOverEvent = new Emitter();
            this.onUnhandledDragOverEvent = this._onUnhandledDragOverEvent.event;
            this.accessor = options.accessor;
            this.addDisposables(this._onDidDrop, this._onUnhandledDragOverEvent);
            if (!options.disableDnd) {
                this.initDragFeatures();
            }
        }
        initDragFeatures() {
            if (!this.header) {
                return;
            }
            const id = this.id;
            const accessorId = this.accessor.id;
            this.header.draggable = true;
            const sharedDragOptions = {
                getData: () => {
                    LocalSelectionTransfer.getInstance().setData([new PaneTransfer(accessorId, id)], PaneTransfer.prototype);
                    return {
                        dispose: () => {
                            LocalSelectionTransfer.getInstance().clearData(PaneTransfer.prototype);
                        },
                    };
                },
            };
            this.html5DragSource = html5Backend.createDragSource(this.header, sharedDragOptions);
            this.pointerDragSource = pointerBackend.createDragSource(this.header, sharedDragOptions);
            const canDisplayOverlay = (event, position) => {
                const data = getPaneData();
                if (data) {
                    if (data.paneId !== this.id &&
                        data.viewId === this.accessor.id) {
                        return true;
                    }
                }
                const firedEvent = new PaneviewUnhandledDragOverEvent(event, position, getPaneData, this);
                this._onUnhandledDragOverEvent.fire(firedEvent);
                return firedEvent.isAccepted;
            };
            const dropTargetOptions = {
                acceptedTargetZones: ['top', 'bottom'],
                overlayModel: {
                    activationSize: { type: 'percentage', value: 50 },
                },
                canDisplayOverlay,
            };
            this.target = html5Backend.createDropTarget(this.element, dropTargetOptions);
            this.pointerTarget = pointerBackend.createDropTarget(this.element, dropTargetOptions);
            this.addDisposables(this._onDidDrop, this.html5DragSource, this.pointerDragSource, this.target, this.pointerTarget, this.target.onDrop((event) => {
                this.onDrop(event);
            }), this.pointerTarget.onDrop((event) => {
                this.onDrop(event);
            }));
        }
        onDrop(event) {
            const data = getPaneData();
            if (!data || data.viewId !== this.accessor.id) {
                // if there is no local drag event for this panel
                // or if the drag event was creating by another Paneview instance
                this._onDidDrop.fire(Object.assign(Object.assign({}, event), { panel: this, api: new PaneviewApi(this.accessor), getData: getPaneData }));
                return;
            }
            const containerApi = this._params
                .containerApi;
            const panelId = data.paneId;
            const existingPanel = containerApi.getPanel(panelId);
            if (!existingPanel) {
                // if the panel doesn't exist
                this._onDidDrop.fire(Object.assign(Object.assign({}, event), { panel: this, getData: getPaneData, api: new PaneviewApi(this.accessor) }));
                return;
            }
            const allPanels = containerApi.panels;
            const fromIndex = allPanels.indexOf(existingPanel);
            let toIndex = containerApi.panels.indexOf(this);
            if (event.position === 'left' || event.position === 'top') {
                toIndex = Math.max(0, toIndex - 1);
            }
            if (event.position === 'right' || event.position === 'bottom') {
                if (fromIndex > toIndex) {
                    toIndex++;
                }
                toIndex = Math.min(allPanels.length - 1, toIndex);
            }
            containerApi.movePanel(fromIndex, toIndex);
        }
    }

    class ContentContainer extends CompositeDisposable {
        get element() {
            return this._element;
        }
        constructor(accessor, group) {
            super();
            this.accessor = accessor;
            this.group = group;
            this.disposable = new MutableDisposable();
            this._onDidFocus = new Emitter();
            this.onDidFocus = this._onDidFocus.event;
            this._onDidBlur = new Emitter();
            this.onDidBlur = this._onDidBlur.event;
            this._element = document.createElement('div');
            this._element.className = 'dv-content-container';
            this._element.tabIndex = -1;
            this.addDisposables(this._onDidFocus, this._onDidBlur);
            const target = group.dropTargetContainer;
            const canDisplayOverlay = (event, position) => {
                if (this.group.locked === 'no-drop-target' ||
                    (this.group.locked && position === 'center')) {
                    return false;
                }
                const data = getPanelData();
                if (!data &&
                    event.shiftKey &&
                    this.group.location.type !== 'floating') {
                    return false;
                }
                if (data && data.viewId === this.accessor.id) {
                    return true;
                }
                return this.group.canDisplayOverlay(event, position, 'content');
            };
            // `dropTarget` stays the concrete `Droptarget` (not via the backend
            // factory) because overlayRenderContainer forwards HTML5 drag events
            // through `dropTarget.dnd` — that field is not part of `IDropTarget`.
            this.dropTarget = new Droptarget(this.element, {
                getOverlayOutline: () => {
                    var _a;
                    return ((_a = accessor.options.theme) === null || _a === void 0 ? void 0 : _a.dndPanelOverlay) === 'group'
                        ? this.element.parentElement
                        : null;
                },
                className: 'dv-drop-target-content',
                acceptedTargetZones: ['top', 'bottom', 'left', 'right', 'center'],
                canDisplayOverlay,
                getOverrideTarget: target ? () => target.model : undefined,
            });
            this.pointerDropTarget = pointerBackend.createDropTarget(this.element, {
                acceptedTargetZones: ['top', 'bottom', 'left', 'right', 'center'],
                canDisplayOverlay,
                getOverlayOutline: () => {
                    var _a;
                    return ((_a = accessor.options.theme) === null || _a === void 0 ? void 0 : _a.dndPanelOverlay) === 'group'
                        ? this.element.parentElement
                        : null;
                },
                className: 'dv-drop-target-content',
                getOverrideTarget: target ? () => target.model : undefined,
            });
            this.addDisposables(this.dropTarget, this.pointerDropTarget);
        }
        show() {
            this.element.style.display = '';
        }
        hide() {
            this.element.style.display = 'none';
        }
        renderPanel(panel, options = { asActive: true }) {
            var _a, _b, _c, _d;
            const doRender = options.asActive ||
                (this.panel && this.group.isPanelActive(this.panel));
            if (this.panel &&
                this.panel.view.content.element.parentElement === this._element) {
                /**
                 * If the currently attached panel is mounted directly to the content then remove it
                 */
                this._element.removeChild(this.panel.view.content.element);
                (_b = (_a = this.panel.view.content).onHide) === null || _b === void 0 ? void 0 : _b.call(_a);
            }
            this.panel = panel;
            let container;
            switch (panel.api.renderer) {
                case 'onlyWhenVisible':
                    this.group.renderContainer.detatch(panel);
                    if (this.panel) {
                        if (doRender) {
                            this._element.appendChild(this.panel.view.content.element);
                            (_d = (_c = this.panel.view.content).onShow) === null || _d === void 0 ? void 0 : _d.call(_c);
                        }
                    }
                    container = this._element;
                    break;
                case 'always':
                    if (panel.view.content.element.parentElement === this._element) {
                        this._element.removeChild(panel.view.content.element);
                    }
                    container = this.group.renderContainer.attach({
                        panel,
                        referenceContainer: this,
                    });
                    break;
                default:
                    throw new Error(`dockview: invalid renderer type '${panel.api.renderer}'`);
            }
            if (doRender) {
                const focusTracker = trackFocus(container);
                this.focusTracker = focusTracker;
                const disposable = new CompositeDisposable();
                disposable.addDisposables(focusTracker, focusTracker.onDidFocus(() => this._onDidFocus.fire()), focusTracker.onDidBlur(() => this._onDidBlur.fire()));
                this.disposable.value = disposable;
            }
        }
        openPanel(panel) {
            if (this.panel === panel) {
                return;
            }
            this.renderPanel(panel);
        }
        layout(_width, _height) {
            // noop
        }
        closePanel() {
            var _a, _b, _c;
            if (this.panel) {
                if (this.panel.api.renderer === 'onlyWhenVisible') {
                    (_a = this.panel.view.content.element.parentElement) === null || _a === void 0 ? void 0 : _a.removeChild(this.panel.view.content.element);
                    (_c = (_b = this.panel.view.content).onHide) === null || _c === void 0 ? void 0 : _c.call(_b);
                }
            }
            this.panel = undefined;
        }
        dispose() {
            this.disposable.dispose();
            super.dispose();
        }
        /**
         * Refresh the focus tracker state to handle cases where focus state
         * gets out of sync due to programmatic panel activation
         */
        refreshFocusState() {
            var _a;
            if ((_a = this.focusTracker) === null || _a === void 0 ? void 0 : _a.refreshState) {
                this.focusTracker.refreshState();
            }
        }
    }

    const DEFAULT_DELAY = 500;
    const DEFAULT_TOLERANCE = 8;
    /**
     * Passive — does not consume the pointer; movement past `tolerance`
     * cancels silently so a sibling `PointerDragSource` can take over.
     */
    class LongPressDetector extends CompositeDisposable {
        constructor(element, options) {
            super();
            this.element = element;
            this.options = options;
            this._startX = 0;
            this._startY = 0;
            this.addDisposables(addDisposableListener(this.element, 'pointerdown', (e) => {
                this._onPointerDown(e);
            }));
        }
        _onPointerDown(event) {
            var _a, _b, _c, _d, _e;
            const touchOnly = (_a = this.options.touchOnly) !== null && _a !== void 0 ? _a : true;
            if (touchOnly &&
                event.pointerType !== 'touch' &&
                event.pointerType !== 'pen') {
                return;
            }
            // Defensive — supersede any in-flight press.
            this._cancelPending();
            this._pointerId = event.pointerId;
            this._startX = event.clientX;
            this._startY = event.clientY;
            const delay = (_b = this.options.delay) !== null && _b !== void 0 ? _b : DEFAULT_DELAY;
            const tolerance = (_c = this.options.tolerance) !== null && _c !== void 0 ? _c : DEFAULT_TOLERANCE;
            // Source's owning window — popout drags fire on their own window.
            const targetWindow = (_e = (_d = this.element.ownerDocument) === null || _d === void 0 ? void 0 : _d.defaultView) !== null && _e !== void 0 ? _e : window;
            this._timer = setTimeout(() => {
                this._timer = undefined;
                this._cancelPending();
                // Touch browsers synthesize a compatibility `contextmenu` event
                // for long-press. preventDefault on the original pointerdown is
                // too late (already dispatched), so install a one-shot
                // capture-phase guard for the next contextmenu. Without this,
                // consumers that don't preventDefault inside their onLongPress
                // (or that early-return before doing so) leak the browser's
                // native menu on top of theirs.
                this._installContextMenuGuard(targetWindow);
                // Same idea for `click`: when the user releases their finger
                // after the long-press, touch browsers dispatch a `click` to
                // the element the touch ended on (the source). Consumers
                // typically wire click to a primary action (e.g. tab activate,
                // tab-group chip collapse-toggle). Without this guard, the
                // long-press immediately fires both the context menu AND the
                // primary action — and the action's side effects (e.g. a chip
                // collapse animation) read as a screen wobble while the menu
                // is supposed to be open. Scoped to the source element so
                // clicks on menu items elsewhere remain effective.
                this._installClickGuard(targetWindow);
                this.options.onLongPress(event);
            }, delay);
            this._moveListener = addDisposableListener(targetWindow, 'pointermove', (moveEvent) => {
                if (moveEvent.pointerId !== this._pointerId) {
                    return;
                }
                const dx = moveEvent.clientX - this._startX;
                const dy = moveEvent.clientY - this._startY;
                if (Math.hypot(dx, dy) > tolerance) {
                    this._cancelPending();
                }
            });
            this._upListener = addDisposableListener(targetWindow, 'pointerup', (upEvent) => {
                if (upEvent.pointerId !== this._pointerId) {
                    return;
                }
                this._cancelPending();
            });
            this._cancelListener = addDisposableListener(targetWindow, 'pointercancel', (cancelEvent) => {
                if (cancelEvent.pointerId !== this._pointerId) {
                    return;
                }
                this._cancelPending();
            });
        }
        _installContextMenuGuard(targetWindow) {
            let guard;
            const timeout = setTimeout(() => guard === null || guard === void 0 ? void 0 : guard.dispose(), 500);
            guard = addDisposableListener(targetWindow, 'contextmenu', (event) => {
                event.preventDefault();
                clearTimeout(timeout);
                guard === null || guard === void 0 ? void 0 : guard.dispose();
            }, { capture: true });
        }
        _installClickGuard(targetWindow) {
            let guard;
            const timeout = setTimeout(() => guard === null || guard === void 0 ? void 0 : guard.dispose(), 500);
            guard = addDisposableListener(targetWindow, 'click', (event) => {
                // Only suppress clicks targeted at the long-pressed element
                // or its descendants. A user tap on a context menu item (or
                // anywhere else) still gets through unchanged.
                const target = event.target;
                if (target && this.element.contains(target)) {
                    event.preventDefault();
                    event.stopPropagation();
                }
                clearTimeout(timeout);
                guard === null || guard === void 0 ? void 0 : guard.dispose();
            }, { capture: true });
        }
        _cancelPending() {
            var _a, _b, _c;
            if (this._timer !== undefined) {
                clearTimeout(this._timer);
                this._timer = undefined;
            }
            this._pointerId = undefined;
            (_a = this._moveListener) === null || _a === void 0 ? void 0 : _a.dispose();
            (_b = this._upListener) === null || _b === void 0 ? void 0 : _b.dispose();
            (_c = this._cancelListener) === null || _c === void 0 ? void 0 : _c.dispose();
            this._moveListener = undefined;
            this._upListener = undefined;
            this._cancelListener = undefined;
        }
        dispose() {
            this._cancelPending();
            super.dispose();
        }
    }

    function resolveDndCapabilities(options) {
        if (options.disableDnd) {
            return { html5: false, pointer: false, pointerHandlesMouse: false };
        }
        switch (options.dndStrategy) {
            case 'pointer':
                return { html5: false, pointer: true, pointerHandlesMouse: true };
            case 'html5':
                return { html5: true, pointer: false, pointerHandlesMouse: false };
            case 'auto':
            case undefined:
            default:
                // On touch-primary devices (phones / basic tablets) HTML5 DnD's
                // native long-press intercepts the gesture before our pointer
                // backend can react — Android Chrome launches a system drag with
                // its half-transparent thumbnail, and the long-press context menu
                // never opens. Disable HTML5 there so the pointer backend owns
                // every gesture. Hybrid devices (touchscreen laptops, Surface,
                // iPad with mouse) keep both backends — mouse uses HTML5, touch
                // falls back to whichever backend the underlying element wired.
                return isCoarsePrimaryInput$2()
                    ? { html5: false, pointer: true, pointerHandlesMouse: true }
                    : { html5: true, pointer: true, pointerHandlesMouse: false };
        }
    }
    function isCoarsePrimaryInput$2() {
        if (typeof window === 'undefined' || !window.matchMedia) {
            return false;
        }
        // Coarse pointer without any fine pointer = phone-class device. A laptop
        // touchscreen reports both, and we want HTML5 to remain available there
        // because a real mouse is also plugged in.
        const coarse = window.matchMedia('(pointer: coarse)').matches;
        const fine = window.matchMedia('(pointer: fine)').matches;
        return coarse && !fine;
    }

    class Tab extends CompositeDisposable {
        get element() {
            return this._element;
        }
        constructor(panel, accessor, group) {
            super();
            this.panel = panel;
            this.accessor = accessor;
            this.group = group;
            this.content = undefined;
            this.panelTransfer = LocalSelectionTransfer.getInstance();
            this._direction = 'horizontal';
            this._onPointDown = new Emitter();
            this.onPointerDown = this._onPointDown.event;
            this._onTabClick = new Emitter();
            this.onTabClick = this._onTabClick.event;
            this._onDropped = new Emitter();
            this.onDrop = this._onDropped.event;
            this._onDragStart = new Emitter();
            this.onDragStart = this._onDragStart.event;
            this._onDragEnd = new Emitter();
            this.onDragEnd = this._onDragEnd.event;
            const caps = resolveDndCapabilities(this.accessor.options);
            this._element = document.createElement('div');
            this._element.className = 'dv-tab';
            this._element.tabIndex = 0;
            this._element.draggable = caps.html5;
            toggleClass(this.element, 'dv-inactive-tab', true);
            const canDisplayOverlay = (event, position) => {
                var _a;
                if (this.group.locked) {
                    return false;
                }
                const data = getPanelData();
                if (data && this.accessor.id === data.viewId) {
                    // Smooth-reorder takes over the in-flight visual when active,
                    // so individual tab overlays are suppressed for internal drags.
                    if (((_a = this.accessor.options.theme) === null || _a === void 0 ? void 0 : _a.tabAnimation) === 'smooth') {
                        return false;
                    }
                    return true;
                }
                return this.group.model.canDisplayOverlay(event, position, 'tab');
            };
            this.dropTarget = html5Backend.createDropTarget(this._element, {
                acceptedTargetZones: ['left', 'right'],
                overlayModel: this._buildOverlayModel(),
                canDisplayOverlay,
                getOverrideTarget: () => { var _a; return (_a = group.model.dropTargetContainer) === null || _a === void 0 ? void 0 : _a.model; },
            });
            this.pointerDropTarget = pointerBackend.createDropTarget(this._element, {
                acceptedTargetZones: ['left', 'right'],
                overlayModel: this._buildOverlayModel(),
                canDisplayOverlay,
                getOverrideTarget: () => { var _a; return (_a = group.model.dropTargetContainer) === null || _a === void 0 ? void 0 : _a.model; },
            });
            const sharedDragOptions = {
                getData: () => {
                    this.panelTransfer.setData([
                        new PanelTransfer(this.accessor.id, this.group.id, this.panel.id),
                    ], PanelTransfer.prototype);
                    return {
                        dispose: () => {
                            this.panelTransfer.clearData(PanelTransfer.prototype);
                        },
                    };
                },
                // 30/-10 matches the HTML5 setDragImage offset that has been
                // shipped for years; pointer backend wraps in PointerGhost,
                // HTML5 backend feeds into setDragImage.
                createGhost: () => ({
                    element: this._buildGhostElement(),
                    offsetX: 30,
                    offsetY: -10,
                }),
                onDragStart: (event) => {
                    var _a;
                    this._onDragStart.fire(event);
                    if (!(event instanceof PointerEvent) &&
                        ((_a = this.accessor.options.theme) === null || _a === void 0 ? void 0 : _a.tabAnimation) === 'smooth') {
                        // Delay collapse to next frame so the browser
                        // captures the full drag image first.
                        requestAnimationFrame(() => {
                            toggleClass(this.element, 'dv-tab--dragging', true);
                        });
                    }
                },
                onDragEnd: (event) => {
                    this._onDragEnd.fire(event);
                },
            };
            this.html5DragSource = html5Backend.createDragSource(this._element, Object.assign(Object.assign({}, sharedDragOptions), { disabled: !caps.html5 }));
            this.pointerDragSource = pointerBackend.createDragSource(this._element, Object.assign(Object.assign({}, sharedDragOptions), { disabled: !caps.pointer, touchOnly: !caps.pointerHandlesMouse, isCancelled: () => !resolveDndCapabilities(this.accessor.options).pointer }));
            // Both droptargets feed the same downstream stream; consumers don't
            // need to know which path produced the overlay.
            this.onWillShowOverlay = exports.DockviewEvent.any(this.dropTarget.onWillShowOverlay, this.pointerDropTarget.onWillShowOverlay);
            this.addDisposables(this._onPointDown, this._onTabClick, this._onDropped, this._onDragStart, this._onDragEnd, this.accessor.onDidOptionsChange(() => {
                const model = this._buildOverlayModel();
                this.dropTarget.setOverlayModel(model);
                this.pointerDropTarget.setOverlayModel(model);
            }), addDisposableListener(this._element, 'dragend', () => {
                // The shared onDragEnd handler already fires _onDragEnd via
                // the HTML5 backend; just strip the dragging class here.
                toggleClass(this.element, 'dv-tab--dragging', false);
            }), this.html5DragSource, addDisposableListener(this._element, 'pointerdown', (event) => {
                this._onPointDown.fire(event);
            }), addDisposableListener(this._element, 'click', (event) => {
                this._onTabClick.fire(event);
            }), addDisposableListener(this._element, 'contextmenu', (event) => {
                this.accessor.contextMenuController.show(this.panel, this.group, event);
            }), new LongPressDetector(this._element, {
                onLongPress: (event) => {
                    // Don't let a subsequent finger move arm a drag on top
                    // of the just-opened menu.
                    this.pointerDragSource.cancelPending();
                    this.accessor.contextMenuController.show(this.panel, this.group, event);
                },
            }), this.dropTarget.onDrop((event) => {
                this._onDropped.fire(event);
            }), this.pointerDropTarget.onDrop((event) => {
                this._onDropped.fire(event);
            }), this.dropTarget, this.pointerDropTarget, this.pointerDragSource);
        }
        setActive(isActive) {
            toggleClass(this.element, 'dv-active-tab', isActive);
            toggleClass(this.element, 'dv-inactive-tab', !isActive);
        }
        setContent(part) {
            if (this.content) {
                this._element.removeChild(this.content.element);
            }
            this.content = part;
            this._element.appendChild(this.content.element);
        }
        _buildOverlayModel() {
            var _a;
            // 'line' themes render a 4px insertion strip at the tab edge via the
            // anchor container's small-boundary path.  'fill' themes render a
            // half-width highlighted area, so we disable the small-boundary path
            // entirely (boundary = 0 ⟹ isSmall always false).
            const smallBoundary = ((_a = this.accessor.options.theme) === null || _a === void 0 ? void 0 : _a.dndTabIndicator) === 'line'
                ? Number.POSITIVE_INFINITY
                : 0;
            return {
                activationSize: { value: 50, type: 'percentage' },
                smallWidthBoundary: smallBoundary,
                smallHeightBoundary: smallBoundary,
            };
        }
        setDirection(direction) {
            this._direction = direction;
            const zones = direction === 'vertical' ? ['top', 'bottom'] : ['left', 'right'];
            this.dropTarget.setTargetZones(zones);
            this.pointerDropTarget.setTargetZones(zones);
        }
        updateDragAndDropState() {
            const caps = resolveDndCapabilities(this.accessor.options);
            this._element.draggable = caps.html5;
            this.html5DragSource.setDisabled(!caps.html5);
            this.pointerDragSource.setDisabled(!caps.pointer);
            this.pointerDragSource.setTouchOnly(!caps.pointerHandlesMouse);
        }
        /**
         * Vertical tabs are flipped to horizontal so the ghost stays readable
         * during the drag rather than appearing sideways-rotated.
         */
        _buildGhostElement() {
            const style = getComputedStyle(this.element);
            const newNode = this.element.cloneNode(true);
            const isVertical = this._direction === 'vertical';
            const verticalSkip = new Set([
                'writing-mode',
                'inline-size',
                'block-size',
                'min-inline-size',
                'min-block-size',
                'max-inline-size',
                'max-block-size',
                'margin-inline',
                'margin-inline-start',
                'margin-inline-end',
                'margin-block',
                'margin-block-start',
                'margin-block-end',
                'padding-inline',
                'padding-inline-start',
                'padding-inline-end',
                'padding-block',
                'padding-block-start',
                'padding-block-end',
            ]);
            Array.from(style).forEach((key) => {
                if (isVertical && verticalSkip.has(key)) {
                    return;
                }
                newNode.style.setProperty(key, style.getPropertyValue(key), style.getPropertyPriority(key));
            });
            if (isVertical) {
                newNode.style.setProperty('writing-mode', 'horizontal-tb');
                newNode.style.setProperty('width', style.height);
                newNode.style.setProperty('height', style.width);
            }
            newNode.style.position = 'absolute';
            newNode.classList.add('dv-tab-ghost-drag');
            return newNode;
        }
    }

    class DockviewWillShowOverlayLocationEvent {
        get kind() {
            return this.options.kind;
        }
        /** Narrow with `instanceof DragEvent` before reading `dataTransfer`. */
        get nativeEvent() {
            return this.event.nativeEvent;
        }
        get position() {
            return this.event.position;
        }
        get defaultPrevented() {
            return this.event.defaultPrevented;
        }
        get panel() {
            return this.options.panel;
        }
        get api() {
            return this.options.api;
        }
        get group() {
            return this.options.group;
        }
        preventDefault() {
            this.event.preventDefault();
        }
        getData() {
            return this.options.getData();
        }
        constructor(event, options) {
            this.event = event;
            this.options = options;
        }
    }

    // Floating-group redock via touch: require a deliberate long press so the
    // "move the float around" gesture doesn't double-trigger the redock ghost.
    // Infinity pressTolerance disables the pre-arm flick override; any motion
    // during the wait is treated as drag-the-float, not redock intent.
    const FLOATING_REDOCK_INITIATION_DELAY_MS = 500;
    class VoidContainer extends CompositeDisposable {
        get element() {
            return this._element;
        }
        constructor(accessor, group) {
            var _a, _b;
            super();
            this.accessor = accessor;
            this.group = group;
            this.panelTransfer = LocalSelectionTransfer.getInstance();
            this._onDrop = new Emitter();
            this.onDrop = this._onDrop.event;
            this._onDragStart = new Emitter();
            this.onDragStart = this._onDragStart.event;
            const caps = resolveDndCapabilities(this.accessor.options);
            this._element = document.createElement('div');
            this._element.className = 'dv-void-container';
            this._element.draggable = caps.html5;
            toggleClass(this._element, 'dv-draggable', caps.html5 || caps.pointer);
            this.addDisposables(this._onDrop, this._onDragStart, addDisposableListener(this._element, 'pointerdown', () => {
                this.accessor.doSetGroupActive(this.group);
            }),
            // Shift+pointerdown marks the event so the group's overlay
            // drag (move-by-floating) sees it was consumed and doesn't
            // fire alongside the HTML5 drag. quasiPreventDefault sets the
            // marker without calling preventDefault — that would also
            // block dragstart, which we need to fire.
            addDisposableListener(this._element, 'pointerdown', (e) => {
                if (e.shiftKey) {
                    quasiPreventDefault(e);
                }
            }, true));
            const canDisplayOverlay = (event, position) => {
                if (this.group.api.locked) {
                    // Dropping on the void/header space adds the panel
                    // to this group, which `locked` is meant to prevent
                    // (both `true` and `'no-drop-target'`).
                    return false;
                }
                const data = getPanelData();
                if (data && this.accessor.id === data.viewId) {
                    return true;
                }
                return group.model.canDisplayOverlay(event, position, 'header_space');
            };
            this.dropTarget = html5Backend.createDropTarget(this._element, {
                acceptedTargetZones: ['center'],
                canDisplayOverlay,
                getOverrideTarget: () => { var _a; return (_a = group.model.dropTargetContainer) === null || _a === void 0 ? void 0 : _a.model; },
            });
            this.pointerDropTarget = pointerBackend.createDropTarget(this._element, {
                acceptedTargetZones: ['center'],
                canDisplayOverlay,
                getOverrideTarget: () => { var _a; return (_a = group.model.dropTargetContainer) === null || _a === void 0 ? void 0 : _a.model; },
            });
            const buildMultiPanelsGhost = () => {
                const ghostEl = document.createElement('div');
                const style = window.getComputedStyle(this._element);
                const bgColor = style.getPropertyValue('--dv-activegroup-visiblepanel-tab-background-color');
                const color = style.getPropertyValue('--dv-activegroup-visiblepanel-tab-color');
                ghostEl.style.backgroundColor = bgColor;
                ghostEl.style.color = color;
                ghostEl.style.padding = '2px 8px';
                ghostEl.style.height = '24px';
                ghostEl.style.fontSize = '11px';
                ghostEl.style.lineHeight = '20px';
                ghostEl.style.borderRadius = '12px';
                ghostEl.style.whiteSpace = 'nowrap';
                ghostEl.style.boxSizing = 'border-box';
                // HTML5 setDragImage snapshots the element as appended to the
                // document; a default block-level div would stretch to the
                // body's width and render as a viewport-wide bar.
                ghostEl.style.display = 'inline-block';
                ghostEl.textContent = `Multiple Panels (${this.group.size})`;
                return ghostEl;
            };
            const buildGhostSpec = () => {
                const createGhost = this.accessor.options.createGroupDragGhostComponent;
                if (createGhost) {
                    const renderer = createGhost(this.group);
                    renderer.init({
                        group: this.group,
                        api: this.accessor.api,
                    });
                    return {
                        element: renderer.element,
                        offsetX: 30,
                        offsetY: -10,
                        dispose: renderer.dispose
                            ? () => { var _a; return (_a = renderer.dispose) === null || _a === void 0 ? void 0 : _a.call(renderer); }
                            : undefined,
                    };
                }
                return {
                    element: buildMultiPanelsGhost(),
                    offsetX: 30,
                    offsetY: -10,
                };
            };
            const sharedDragOptions = {
                getData: () => {
                    this.panelTransfer.setData([new PanelTransfer(this.accessor.id, this.group.id, null)], PanelTransfer.prototype);
                    return {
                        dispose: () => {
                            this.panelTransfer.clearData(PanelTransfer.prototype);
                        },
                    };
                },
                createGhost: buildGhostSpec,
                onDragStart: (event) => {
                    this._onDragStart.fire(event);
                },
            };
            this.html5DragSource = html5Backend.createDragSource(this._element, Object.assign(Object.assign({}, sharedDragOptions), { disabled: !caps.html5, isCancelled: (event) => {
                    // HTML5: floating groups need shift+drag as the explicit
                    // detach gesture (otherwise click-and-drag conflicts with
                    // moving the floating group itself).
                    if (this.group.api.location.type === 'floating' &&
                        !event.shiftKey) {
                        return true;
                    }
                    if (this.group.api.location.type === 'edge' &&
                        this.group.size === 0) {
                        return true;
                    }
                    return false;
                } }));
            const isFloating = () => { var _a, _b, _c; return ((_c = (_b = (_a = this.group) === null || _a === void 0 ? void 0 : _a.api) === null || _b === void 0 ? void 0 : _b.location) === null || _c === void 0 ? void 0 : _c.type) === 'floating'; };
            this.pointerDragSource = pointerBackend.createDragSource(this._element, Object.assign(Object.assign({}, sharedDragOptions), { disabled: !caps.pointer, touchOnly: !caps.pointerHandlesMouse,
                // Floating groups share this element with the overlay's
                // move-the-float drag. Without a longer hold + tolerance
                // override, both gestures commit simultaneously and the
                // user sees the float follow their finger *and* a ghost.
                touchInitiationDelay: () => isFloating() ? FLOATING_REDOCK_INITIATION_DELAY_MS : 250, pressTolerance: () => (isFloating() ? Infinity : 8), isCancelled: () => {
                    if (!resolveDndCapabilities(this.accessor.options).pointer) {
                        return true;
                    }
                    // Pointer: long-press IS the deliberate gesture, so
                    // floating groups don't need the shift gate.
                    if (this.group.api.location.type === 'edge' &&
                        this.group.size === 0) {
                        return true;
                    }
                    return false;
                }, onDragStart: (event) => {
                    var _a;
                    // Redock just committed — abort any in-flight overlay
                    // move so the float stops following the finger while
                    // the ghost takes over.
                    (_a = this.getFloatingOverlay()) === null || _a === void 0 ? void 0 : _a.cancelPendingDrag();
                    this._onDragStart.fire(event);
                } }));
            // Mirror direction: once the overlay's move-the-float gesture has
            // actually moved something, cancel the pending redock arm so the
            // ghost doesn't appear mid-drag if the user holds past 500ms.
            const overlayMoveSub = new MutableDisposable();
            const refreshOverlayMoveSub = () => {
                const overlay = this.getFloatingOverlay();
                overlayMoveSub.value = overlay
                    ? overlay.onDidStartMoving(() => {
                        this.pointerDragSource.cancelPending();
                    })
                    : exports.DockviewDisposable.NONE;
            };
            refreshOverlayMoveSub();
            this.addDisposables(overlayMoveSub);
            const locationChange = (_b = (_a = this.group) === null || _a === void 0 ? void 0 : _a.api) === null || _b === void 0 ? void 0 : _b.onDidLocationChange;
            if (locationChange) {
                this.addDisposables(locationChange(refreshOverlayMoveSub));
            }
            this.onWillShowOverlay = exports.DockviewEvent.any(this.dropTarget.onWillShowOverlay, this.pointerDropTarget.onWillShowOverlay);
            this.addDisposables(this.html5DragSource, this.dropTarget.onDrop((event) => {
                this._onDrop.fire(event);
            }), this.pointerDropTarget.onDrop((event) => {
                this._onDrop.fire(event);
            }), this.dropTarget, this.pointerDropTarget, this.pointerDragSource);
        }
        updateDragAndDropState() {
            const caps = resolveDndCapabilities(this.accessor.options);
            this._element.draggable = caps.html5;
            toggleClass(this._element, 'dv-draggable', caps.html5 || caps.pointer);
            this.html5DragSource.setDisabled(!caps.html5);
            this.pointerDragSource.setDisabled(!caps.pointer);
            this.pointerDragSource.setTouchOnly(!caps.pointerHandlesMouse);
        }
        getFloatingOverlay() {
            var _a, _b;
            if (!this.group) {
                return undefined;
            }
            return (_b = (_a = this.accessor.floatingGroups) === null || _a === void 0 ? void 0 : _a.find((fg) => fg.group === this.group)) === null || _b === void 0 ? void 0 : _b.overlay;
        }
    }

    class Scrollbar extends CompositeDisposable {
        get element() {
            return this._element;
        }
        get orientation() {
            return this._orientation;
        }
        set orientation(value) {
            if (this._orientation === value) {
                return;
            }
            this._scrollOffset = 0;
            this._orientation = value;
            removeClasses(this._scrollbar, 'dv-scrollbar-vertical', 'dv-scrollbar-horizontal');
            if (value === 'vertical') {
                addClasses(this._scrollbar, 'dv-scrollbar-vertical');
            }
            else {
                addClasses(this._scrollbar, 'dv-scrollbar-horizontal');
            }
        }
        constructor(scrollableElement) {
            super();
            this.scrollableElement = scrollableElement;
            this._scrollOffset = 0;
            this._orientation = 'horizontal';
            this._element = document.createElement('div');
            this._element.className = 'dv-scrollable';
            this._scrollbar = document.createElement('div');
            this._scrollbar.className = 'dv-scrollbar dv-scrollbar-horizontal';
            this.element.appendChild(scrollableElement);
            this.element.appendChild(this._scrollbar);
            this.addDisposables(addDisposableListener(this.element, 'wheel', (event) => {
                this._scrollOffset += event.deltaY * Scrollbar.MouseWheelSpeed;
                this.calculateScrollbarStyles();
            }), addDisposableListener(this._scrollbar, 'pointerdown', (event) => {
                event.preventDefault();
                toggleClass(this.element, 'dv-scrollable-scrolling', true);
                const originalClient = this._orientation === 'horizontal'
                    ? event.clientX
                    : event.clientY;
                const originalScrollOffset = this._scrollOffset;
                const onPointerMove = (event) => {
                    const delta = this._orientation === 'horizontal'
                        ? event.clientX - originalClient
                        : event.clientY - originalClient;
                    const clientSize = this._orientation === 'horizontal'
                        ? this.element.clientWidth
                        : this.element.clientHeight;
                    const scrollSize = this._orientation === 'horizontal'
                        ? this.scrollableElement.scrollWidth
                        : this.scrollableElement.scrollHeight;
                    const p = clientSize / scrollSize;
                    this._scrollOffset = originalScrollOffset + delta / p;
                    this.calculateScrollbarStyles();
                };
                const onEnd = () => {
                    toggleClass(this.element, 'dv-scrollable-scrolling', false);
                    document.removeEventListener('pointermove', onPointerMove);
                    document.removeEventListener('pointerup', onEnd);
                    document.removeEventListener('pointercancel', onEnd);
                };
                document.addEventListener('pointermove', onPointerMove);
                document.addEventListener('pointerup', onEnd);
                document.addEventListener('pointercancel', onEnd);
            }), addDisposableListener(this.element, 'scroll', () => {
                this.calculateScrollbarStyles();
            }), addDisposableListener(this.scrollableElement, 'scroll', () => {
                this._scrollOffset =
                    this._orientation === 'horizontal'
                        ? this.scrollableElement.scrollLeft
                        : this.scrollableElement.scrollTop;
                this.calculateScrollbarStyles();
            }), watchElementResize(this.element, () => {
                toggleClass(this.element, 'dv-scrollable-resizing', true);
                if (this._animationTimer) {
                    clearTimeout(this._animationTimer);
                }
                this._animationTimer = setTimeout(() => {
                    clearTimeout(this._animationTimer);
                    toggleClass(this.element, 'dv-scrollable-resizing', false);
                }, 500);
                this.calculateScrollbarStyles();
            }));
        }
        calculateScrollbarStyles() {
            const clientSize = this._orientation === 'horizontal'
                ? this.element.clientWidth
                : this.element.clientHeight;
            const scrollSize = this._orientation === 'horizontal'
                ? this.scrollableElement.scrollWidth
                : this.scrollableElement.scrollHeight;
            const hasScrollbar = scrollSize > clientSize;
            if (hasScrollbar) {
                const px = clientSize * (clientSize / scrollSize);
                if (this._orientation === 'horizontal') {
                    this._scrollbar.style.width = `${px}px`;
                    this._scrollbar.style.height = '';
                }
                else {
                    this._scrollbar.style.height = `${px}px`;
                    this._scrollbar.style.width = '';
                }
                this._scrollOffset = clamp(this._scrollOffset, 0, scrollSize - clientSize);
                if (this._orientation === 'horizontal') {
                    this.scrollableElement.scrollLeft = this._scrollOffset;
                }
                else {
                    this.scrollableElement.scrollTop = this._scrollOffset;
                }
                const percentageComplete = this._scrollOffset / (scrollSize - clientSize);
                if (this._orientation === 'horizontal') {
                    this._scrollbar.style.left = `${(clientSize - px) * percentageComplete}px`;
                    this._scrollbar.style.top = '';
                }
                else {
                    this._scrollbar.style.top = `${(clientSize - px) * percentageComplete}px`;
                    this._scrollbar.style.left = '';
                }
            }
            else {
                if (this._orientation === 'horizontal') {
                    this._scrollbar.style.width = '0px';
                    this._scrollbar.style.left = '0px';
                }
                else {
                    this._scrollbar.style.height = '0px';
                    this._scrollbar.style.top = '0px';
                }
                this._scrollOffset = 0;
            }
        }
    }
    Scrollbar.MouseWheelSpeed = 1;

    const DEFAULT_TAB_GROUP_COLORS = [
        { id: 'grey', value: 'var(--dv-tab-group-color-grey)', label: 'Grey' },
        { id: 'blue', value: 'var(--dv-tab-group-color-blue)', label: 'Blue' },
        { id: 'red', value: 'var(--dv-tab-group-color-red)', label: 'Red' },
        {
            id: 'yellow',
            value: 'var(--dv-tab-group-color-yellow)',
            label: 'Yellow',
        },
        { id: 'green', value: 'var(--dv-tab-group-color-green)', label: 'Green' },
        { id: 'pink', value: 'var(--dv-tab-group-color-pink)', label: 'Pink' },
        {
            id: 'purple',
            value: 'var(--dv-tab-group-color-purple)',
            label: 'Purple',
        },
        { id: 'cyan', value: 'var(--dv-tab-group-color-cyan)', label: 'Cyan' },
        {
            id: 'orange',
            value: 'var(--dv-tab-group-color-orange)',
            label: 'Orange',
        },
    ];
    /**
     * Runtime palette for tab-group color accents.
     *
     * Resolves a stored `color` string to a CSS color expression, with three
     * fall-through modes:
     *   1. `id` matches an entry → entry's `value`
     *   2. `id` doesn't match → `id` itself (raw CSS literal pass-through)
     *   3. `id` is empty or undefined → undefined (caller skips assignment)
     *
     * When `enabled` is false the palette returns undefined for everything; this
     * is the `tabGroupAccent: 'off'` opt-out path.
     */
    class TabGroupColorPalette {
        constructor(entries, enabled = true) {
            this._entries = entries.slice();
            this._byId = new Map(entries.map((e) => [e.id, e]));
            this._enabled = enabled;
        }
        get enabled() {
            return this._enabled;
        }
        set enabled(value) {
            this._enabled = value;
        }
        /**
         * Replace the entry list in place. Used by `updateOptions` so that
         * existing palette references (held by chips, indicators, etc.) see
         * the new palette without needing to be re-wired.
         */
        setEntries(entries) {
            this._entries = entries.slice();
            this._byId = new Map(entries.map((e) => [e.id, e]));
        }
        entries() {
            return this._entries;
        }
        has(id) {
            return this._byId.has(id);
        }
        get(id) {
            return this._byId.get(id);
        }
        /** First entry's id; used as the default when a color is unset. */
        defaultId() {
            var _a;
            return (_a = this._entries[0]) === null || _a === void 0 ? void 0 : _a.id;
        }
        /**
         * Resolve a stored color to its CSS value, or undefined if no value
         * should be written (palette disabled, or color empty/undefined).
         */
        resolveValue(color) {
            if (!this._enabled || !color) {
                return undefined;
            }
            const entry = this._byId.get(color);
            return entry ? entry.value : color;
        }
    }
    let _fallbackPalette;
    /**
     * Lazy-built palette used when the accessor isn't available (test mocks,
     * isolated chip construction). Production code paths always pass a real
     * palette through.
     */
    function getFallbackPalette() {
        if (!_fallbackPalette) {
            _fallbackPalette = new TabGroupColorPalette(DEFAULT_TAB_GROUP_COLORS, true);
        }
        return _fallbackPalette;
    }
    /**
     * Set the `--dv-tab-group-color` custom property on `el` to the resolved
     * accent value, or remove it when the palette is disabled / color is unset.
     */
    function applyTabGroupAccent(el, color, palette) {
        const value = (palette !== null && palette !== void 0 ? palette : getFallbackPalette()).resolveValue(color);
        if (value === undefined) {
            el.style.removeProperty('--dv-tab-group-color');
        }
        else {
            el.style.setProperty('--dv-tab-group-color', value);
        }
    }
    /**
     * Return the resolved CSS color for a tab group, or undefined when the
     * palette is disabled or no color is set. Use this when you need the raw
     * value to assign to a non-custom-property style (e.g. SVG stroke,
     * backgroundColor on the indicator underline).
     */
    function resolveTabGroupAccent(color, palette) {
        return (palette !== null && palette !== void 0 ? palette : getFallbackPalette()).resolveValue(color);
    }

    /**
     * Visual chip for a tab group. Owns the DOM element, label, click /
     * context-menu interactions, and exposes a long-press gesture as a
     * second `onContextMenu` source. Drag-and-drop wiring lives in
     * `TabGroupManager` — the manager constructs the drag sources on this
     * chip's element so it can include tabs-list context (custom group
     * drag image, tab-group transfer payload).
     */
    class TabGroupChip extends CompositeDisposable {
        get element() {
            return this._element;
        }
        constructor(_palette) {
            super();
            this._palette = _palette;
            this._onClick = new Emitter();
            this.onClick = this._onClick.event;
            this._onContextMenu = new Emitter();
            /** Fires on right-click and on touch long-press. */
            this.onContextMenu = this._onContextMenu.event;
            this._element = document.createElement('div');
            this._element.className = 'dv-tab-group-chip';
            this._element.tabIndex = 0;
            this._label = document.createElement('span');
            this._label.className = 'dv-tab-group-chip-label';
            this._element.appendChild(this._label);
            this.addDisposables(this._onClick, this._onContextMenu, new LongPressDetector(this._element, {
                onLongPress: (event) => {
                    this._onContextMenu.fire(event);
                },
            }), addDisposableListener(this._element, 'click', (event) => {
                this._onClick.fire(event);
            }), addDisposableListener(this._element, 'contextmenu', (event) => {
                this._onContextMenu.fire(event);
            }));
        }
        init(params) {
            this._tabGroup = params.tabGroup;
            this.updateColor(params.tabGroup.color);
            this.updateLabel(params.tabGroup.label);
            this.updateCollapsed(params.tabGroup.collapsed);
            this.addDisposables(params.tabGroup.onDidChange(() => {
                if (this._tabGroup) {
                    this.updateColor(this._tabGroup.color);
                    this.updateLabel(this._tabGroup.label);
                }
            }), params.tabGroup.onDidCollapseChange((collapsed) => {
                this.updateCollapsed(collapsed);
            }), this._onClick.event(() => {
                var _a;
                (_a = this._tabGroup) === null || _a === void 0 ? void 0 : _a.toggle();
            }));
        }
        update(params) {
            this._tabGroup = params.tabGroup;
            this.updateColor(params.tabGroup.color);
            this.updateLabel(params.tabGroup.label);
            this.updateCollapsed(params.tabGroup.collapsed);
        }
        updateColor(color) {
            var _a;
            applyTabGroupAccent(this._element, color, this._palette);
            toggleClass(this._element, 'dv-tab-group-chip--accent-off', ((_a = this._palette) === null || _a === void 0 ? void 0 : _a.enabled) === false);
        }
        updateLabel(label) {
            this._label.textContent = label;
            toggleClass(this._label, 'dv-tab-group-chip-label--empty', !label);
        }
        updateCollapsed(collapsed) {
            toggleClass(this._element, 'dv-tab-group-chip--collapsed', collapsed);
        }
    }

    /**
     * Shared positioning logic for tab group indicators.
     * Subclasses implement `applyShape` to control the visual output.
     */
    class BaseTabGroupIndicator {
        get underlines() {
            return this._underlines;
        }
        constructor(_ctx) {
            this._ctx = _ctx;
            this._underlines = new Map();
            this._rafId = null;
        }
        positionUnderlines() {
            requestAnimationFrame(() => {
                this._positionUnderlinesSync();
            });
        }
        /**
         * Continuously reposition underlines every frame for the duration
         * of a tab transition (~200ms), so the underline tracks tab sizes.
         */
        trackUnderlines() {
            if (this._rafId !== null) {
                cancelAnimationFrame(this._rafId);
            }
            const start = performance.now();
            const duration = 250; // slightly longer than transition to ensure we catch the end
            const tick = () => {
                this._positionUnderlinesSync();
                if (performance.now() - start < duration) {
                    this._rafId = requestAnimationFrame(tick);
                }
                else {
                    this._rafId = null;
                }
            };
            this._rafId = requestAnimationFrame(tick);
        }
        syncUnderlineElements(activeGroupIds) {
            // Ensure underline elements exist for active groups
            for (const groupId of activeGroupIds) {
                if (!this._underlines.has(groupId)) {
                    const underline = document.createElement('div');
                    underline.className = 'dv-tab-group-underline';
                    this._ctx.tabsList.appendChild(underline);
                    this._underlines.set(groupId, underline);
                }
            }
            // Remove underlines for dissolved groups
            for (const [groupId, el] of this._underlines) {
                if (!activeGroupIds.has(groupId)) {
                    el.remove();
                    this._underlines.delete(groupId);
                }
            }
        }
        getUnderline(groupId) {
            return this._underlines.get(groupId);
        }
        dispose() {
            if (this._rafId !== null) {
                cancelAnimationFrame(this._rafId);
                this._rafId = null;
            }
            for (const [, el] of this._underlines) {
                el.remove();
            }
            this._underlines.clear();
        }
        _positionUnderlinesSync() {
            const containerRect = this._ctx.tabsList.getBoundingClientRect();
            const tabGroups = this._ctx.getTabGroups();
            const isVertical = this._ctx.getDirection() === 'vertical';
            const containerCrossSize = isVertical
                ? containerRect.width
                : containerRect.height;
            const activePanelId = this._ctx.getActivePanelId();
            const tabMap = this._ctx.getTabMap();
            for (const tg of tabGroups) {
                const underline = this._underlines.get(tg.id);
                if (!underline) {
                    continue;
                }
                const panelIds = tg.panelIds;
                if (panelIds.length === 0) {
                    underline.style.display = 'none';
                    continue;
                }
                underline.style.display = '';
                const chipEl = this._ctx.getChipElement(tg.id);
                // In vertical mode, compute top/bottom edges; in horizontal, left/right.
                let startEdge;
                if (chipEl) {
                    const chipRect = chipEl.getBoundingClientRect();
                    const chipStyle = getComputedStyle(chipEl);
                    const leadingMargin = isVertical
                        ? Number.parseFloat(chipStyle.marginTop) || 0
                        : Number.parseFloat(chipStyle.marginLeft) || 0;
                    startEdge = isVertical
                        ? chipRect.top - containerRect.top - leadingMargin
                        : chipRect.left - containerRect.left - leadingMargin;
                }
                else {
                    const firstPanelId = panelIds[0];
                    const firstTabEntry = tabMap.get(firstPanelId);
                    if (firstTabEntry) {
                        const firstRect = firstTabEntry.value.element.getBoundingClientRect();
                        startEdge = isVertical
                            ? firstRect.top - containerRect.top
                            : firstRect.left - containerRect.left;
                    }
                    else {
                        startEdge = 0;
                    }
                }
                // Measure the actual last tab position (follows CSS transitions in real-time)
                const lastPanelId = panelIds[panelIds.length - 1];
                const lastTabEntry = tabMap.get(lastPanelId);
                if (!lastTabEntry) {
                    if (isVertical) {
                        underline.style.top = `${startEdge}px`;
                        underline.style.height = '0px';
                        underline.style.left = '';
                        underline.style.width = '';
                    }
                    else {
                        underline.style.left = `${startEdge}px`;
                        underline.style.width = '0px';
                        underline.style.top = '';
                        underline.style.height = '';
                    }
                    continue;
                }
                const lastTabRect = lastTabEntry.value.element.getBoundingClientRect();
                let endEdge = isVertical
                    ? lastTabRect.bottom - containerRect.top
                    : lastTabRect.right - containerRect.left;
                let span = endEdge - startEdge;
                // During collapse or expand: converge both edges toward chip center
                const isAnimating = tg.collapsed ||
                    tg.panelIds.some((pid) => {
                        const te = tabMap.get(pid);
                        return (te &&
                            te.value.element.classList.contains('dv-tab--group-expanding'));
                    });
                if (isAnimating && chipEl) {
                    const chipRect = chipEl.getBoundingClientRect();
                    const chipCenter = isVertical
                        ? chipRect.top + chipRect.height / 2 - containerRect.top
                        : chipRect.left + chipRect.width / 2 - containerRect.left;
                    // Sum of current visible tab sizes (shrinking or growing)
                    let currentTabSize = 0;
                    let fullTabSize = 0;
                    for (const pid of tg.panelIds) {
                        const te = tabMap.get(pid);
                        if (!te)
                            continue;
                        const el = te.value.element;
                        if (isVertical) {
                            currentTabSize += el.getBoundingClientRect().height;
                            fullTabSize += el.scrollHeight;
                        }
                        else {
                            currentTabSize += el.getBoundingClientRect().width;
                            fullTabSize += el.scrollWidth;
                        }
                    }
                    // progress: 0 when tabs at 0 size, 1 when fully open
                    const progress = fullTabSize > 0
                        ? Math.min(1, currentTabSize / fullTabSize)
                        : 0;
                    // Interpolate start and end edges toward chip center
                    startEdge = chipCenter + (startEdge - chipCenter) * progress;
                    endEdge = chipCenter + (endEdge - chipCenter) * progress;
                    span = Math.max(0, endEdge - startEdge);
                }
                if (isVertical) {
                    underline.style.top = `${startEdge}px`;
                    underline.style.height = `${Math.max(0, span)}px`;
                    // Clear horizontal properties
                    underline.style.left = '';
                    underline.style.width = '';
                }
                else {
                    underline.style.left = `${startEdge}px`;
                    underline.style.width = `${Math.max(0, span)}px`;
                    // Clear vertical properties
                    underline.style.top = '';
                    underline.style.height = '';
                }
                this.applyShape(underline, tg, startEdge, span, containerCrossSize, activePanelId, containerRect, isVertical);
            }
        }
    }
    /**
     * Chrome-style wrap-around indicator using SVG paths.
     */
    class WrapTabGroupIndicator extends BaseTabGroupIndicator {
        _applyStraightLine(svg, path, underline, t, mainSize, isVertical) {
            if (isVertical) {
                svg.setAttribute('width', String(t));
                svg.setAttribute('height', String(mainSize));
                underline.style.width = `${t}px`;
                underline.style.height = `${mainSize}px`;
                path.setAttribute('d', `M ${t / 2},0 L ${t / 2},${mainSize}`);
            }
            else {
                svg.setAttribute('width', String(mainSize));
                svg.setAttribute('height', String(t));
                underline.style.width = `${mainSize}px`;
                underline.style.height = `${t}px`;
                path.setAttribute('d', `M 0,${t / 2} L ${mainSize},${t / 2}`);
            }
        }
        /**
         * Chrome-style wrap-around underline: a stroked SVG path that runs
         * along the bottom (or left edge in vertical mode), curving up and
         * over the active tab with rounded corners.
         *
         * The SVG and path elements are created once per underline and reused;
         * only the `d`, `stroke`, and viewport attributes are updated each frame.
         */
        applyShape(underline, tg, groupStart, groupSpan, containerCrossSize, activePanelId, containerRect, isVertical) {
            const t = 2; // line thickness in px
            const crossSize = containerCrossSize;
            const mainSize = groupSpan;
            const color = resolveTabGroupAccent(tg.color, this._ctx.getColorPalette());
            if (mainSize <= 0 || crossSize <= 0 || color === undefined) {
                underline.style.display = 'none';
                return;
            }
            underline.style.display = '';
            // Find the active tab within this group
            let activeTabEntry;
            if (activePanelId && tg.panelIds.includes(activePanelId)) {
                activeTabEntry = this._ctx.getTabMap().get(activePanelId);
            }
            // Ensure SVG + path child exists (created once, reused)
            let svg = underline.firstElementChild;
            let path;
            if (!svg || svg.tagName !== 'svg') {
                underline.replaceChildren();
                svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
                svg.style.display = 'block';
                path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
                path.setAttribute('fill', 'none');
                svg.appendChild(path);
                underline.appendChild(svg);
            }
            else {
                path = svg.firstElementChild;
            }
            path.setAttribute('stroke', color);
            path.setAttribute('stroke-width', String(t));
            if (!activeTabEntry) {
                this._applyStraightLine(svg, path, underline, t, mainSize, isVertical);
                return;
            }
            const activeRect = activeTabEntry.value.element.getBoundingClientRect();
            // Compute active tab start/end relative to the group start
            let aStart;
            let aEnd;
            if (isVertical) {
                aStart = Math.max(0, activeRect.top - containerRect.top - groupStart);
                aEnd = Math.min(mainSize, activeRect.bottom - containerRect.top - groupStart);
            }
            else {
                aStart = Math.max(0, activeRect.left - containerRect.left - groupStart);
                aEnd = Math.min(mainSize, activeRect.right - containerRect.left - groupStart);
            }
            if (aEnd <= aStart) {
                this._applyStraightLine(svg, path, underline, t, mainSize, isVertical);
                return;
            }
            const r = 6; // corner radius
            const half = t / 2;
            if (isVertical) {
                const svgW = crossSize;
                const svgH = mainSize;
                svg.setAttribute('width', String(svgW));
                svg.setAttribute('height', String(svgH));
                underline.style.width = `${svgW}px`;
                underline.style.height = `${svgH}px`;
                const xLeft = half;
                const xRight = svgW - half;
                const d = [
                    `M ${xLeft},0`,
                    `L ${xLeft},${aStart - r}`,
                    `Q ${xLeft},${aStart} ${xLeft + r},${aStart}`,
                    `L ${xRight - r},${aStart}`,
                    `Q ${xRight},${aStart} ${xRight},${aStart + r}`,
                    `L ${xRight},${aEnd - r}`,
                    `Q ${xRight},${aEnd} ${xRight - r},${aEnd}`,
                    `L ${xLeft + r},${aEnd}`,
                    `Q ${xLeft},${aEnd} ${xLeft},${aEnd + r}`,
                    `L ${xLeft},${svgH}`,
                ].join(' ');
                path.setAttribute('d', d);
            }
            else {
                const svgW = mainSize;
                const svgH = crossSize;
                svg.setAttribute('width', String(svgW));
                svg.setAttribute('height', String(svgH));
                underline.style.width = `${svgW}px`;
                underline.style.height = `${svgH}px`;
                const yBot = svgH - half;
                const yTop = half;
                const d = [
                    `M 0,${yBot}`,
                    `L ${aStart - r},${yBot}`,
                    `Q ${aStart},${yBot} ${aStart},${yBot - r}`,
                    `L ${aStart},${yTop + r}`,
                    `Q ${aStart},${yTop} ${aStart + r},${yTop}`,
                    `L ${aEnd - r},${yTop}`,
                    `Q ${aEnd},${yTop} ${aEnd},${yTop + r}`,
                    `L ${aEnd},${yBot - r}`,
                    `Q ${aEnd},${yBot} ${aEnd + r},${yBot}`,
                    `L ${svgW},${yBot}`,
                ].join(' ');
                path.setAttribute('d', d);
            }
        }
    }
    /**
     * Flat continuous bar indicator — no wrap-around, just a colored line
     * spanning the full tab group width.
     */
    class NoneTabGroupIndicator extends BaseTabGroupIndicator {
        applyShape(underline, tg, _startEdge, span, _containerCrossSize, _activePanelId, _containerRect, isVertical) {
            const t = 2; // line thickness in px
            const color = resolveTabGroupAccent(tg.color, this._ctx.getColorPalette());
            if (span <= 0 || color === undefined) {
                underline.style.display = 'none';
                return;
            }
            underline.style.display = '';
            // Clear any SVG content left over from a mode switch
            if (underline.firstElementChild) {
                underline.replaceChildren();
            }
            underline.style.backgroundColor = color;
            if (isVertical) {
                underline.style.width = `${t}px`;
                underline.style.height = `${span}px`;
            }
            else {
                underline.style.width = `${span}px`;
                underline.style.height = `${t}px`;
            }
        }
    }

    const EMPTY_MAP = new Map();
    class TabGroupManager {
        get chipRenderers() {
            return this._chipRenderers;
        }
        get groupUnderlines() {
            var _a, _b;
            return (_b = (_a = this._indicator) === null || _a === void 0 ? void 0 : _a.underlines) !== null && _b !== void 0 ? _b : EMPTY_MAP;
        }
        get skipNextCollapseAnimation() {
            return this._skipNextCollapseAnimation;
        }
        set skipNextCollapseAnimation(value) {
            this._skipNextCollapseAnimation = value;
        }
        constructor(_ctx, _callbacks) {
            this._ctx = _ctx;
            this._callbacks = _callbacks;
            this._chipRenderers = new Map();
            this._indicator = null;
            this._skipNextCollapseAnimation = false;
            this._pendingTransitionCleanups = new Map();
        }
        /**
         * Synchronize chip elements and CSS classes for all tab groups
         * in the parent group model. Call after any tab group mutation.
         */
        update() {
            const model = this._ctx.group.model;
            const tabGroups = model.getTabGroups();
            // Track which group IDs are still active
            const activeGroupIds = new Set();
            for (const tabGroup of tabGroups) {
                activeGroupIds.add(tabGroup.id);
                this._ensureChipForGroup(tabGroup);
                this._positionChipForGroup(tabGroup);
            }
            // Remove chips for dissolved/destroyed groups
            for (const [groupId, entry] of this._chipRenderers) {
                if (!activeGroupIds.has(groupId)) {
                    entry.chip.element.remove();
                    entry.chip.dispose();
                    entry.disposable.dispose();
                    this._chipRenderers.delete(groupId);
                }
            }
            // Update CSS classes on all tabs
            this._updateTabGroupClasses();
        }
        /**
         * Re-read the active palette and re-apply colors to chips, tabs and
         * the indicator. Called when `tabGroupColors` / `tabGroupAccent`
         * options change at runtime.
         */
        refreshAccents() {
            var _a, _b;
            for (const tabGroup of this._ctx.group.model.getTabGroups()) {
                const entry = this._chipRenderers.get(tabGroup.id);
                (_b = entry === null || entry === void 0 ? void 0 : (_a = entry.chip).update) === null || _b === void 0 ? void 0 : _b.call(_a, { tabGroup });
            }
            this._updateTabGroupClasses();
        }
        positionAllChips() {
            if (this._chipRenderers.size === 0) {
                return;
            }
            for (const tabGroup of this._ctx.group.model.getTabGroups()) {
                this._positionChipForGroup(tabGroup);
            }
        }
        updateDirection() {
            const isVertical = this._ctx.getDirection() === 'vertical';
            for (const [, entry] of this._chipRenderers) {
                entry.dropTarget.setTargetZones(isVertical ? ['top'] : ['left']);
            }
        }
        snapshotChipWidths() {
            const widths = new Map();
            for (const [groupId, entry] of this._chipRenderers) {
                widths.set(groupId, entry.chip.element.getBoundingClientRect().width);
            }
            return widths;
        }
        positionUnderlines() {
            var _a;
            (_a = this._indicator) === null || _a === void 0 ? void 0 : _a.positionUnderlines();
        }
        trackUnderlines() {
            var _a;
            (_a = this._indicator) === null || _a === void 0 ? void 0 : _a.trackUnderlines();
        }
        setGroupDragImage(event, tabGroup, chipEl) {
            if (!event.dataTransfer) {
                return;
            }
            const isVertical = this._ctx.getDirection() === 'vertical';
            // Clone the entire tabs list so cloned nodes inherit all
            // theme styles, CSS variables and class-based rules.
            const clone = this._ctx.tabsList.cloneNode(true);
            if (isVertical) {
                // Force horizontal orientation for the drag ghost by
                // removing vertical CSS classes and overriding writing-mode.
                clone.classList.remove('dv-tabs-container-vertical', 'dv-vertical');
                clone.classList.add('dv-horizontal');
                clone.style.writingMode = 'horizontal-tb';
                clone.style.height = `${this._ctx.tabsList.offsetWidth}px`;
            }
            else {
                clone.style.height = `${this._ctx.tabsList.offsetHeight}px`;
            }
            clone.style.width = 'auto';
            clone.style.overflow = 'visible';
            clone.style.pointerEvents = 'none';
            // Remove all elements except the chip so the drag ghost
            // shows only the chip regardless of the group's expanded state.
            const children = Array.from(clone.children);
            const realChildren = Array.from(this._ctx.tabsList.children);
            for (let i = children.length - 1; i >= 0; i--) {
                const real = realChildren[i];
                if (real === chipEl) {
                    continue; // keep the chip only
                }
                children[i].remove();
            }
            // Wrap the clone in a minimal ancestor chain so that CSS
            // selectors like `.dv-groupview.dv-active-group > .dv-tabs-and-actions-container .dv-tabs-container > .dv-tab`
            // match the cloned tabs and apply correct color/background.
            const wrapper = document.createElement('div');
            wrapper.className = 'dv-groupview dv-active-group';
            wrapper.style.position = 'fixed';
            wrapper.style.top = '-10000px';
            wrapper.style.left = '0px';
            wrapper.style.height = 'auto';
            wrapper.style.width = 'auto';
            wrapper.style.pointerEvents = 'none';
            const actionsWrapper = document.createElement('div');
            actionsWrapper.className = 'dv-tabs-and-actions-container';
            actionsWrapper.style.height = 'auto';
            actionsWrapper.style.width = 'auto';
            wrapper.appendChild(actionsWrapper);
            actionsWrapper.appendChild(clone);
            // Append inside the dockview root so CSS variables are inherited
            this._ctx.accessor.element.appendChild(wrapper);
            // Compute cursor offset relative to the wrapper element.
            // The cloned chip is the first .dv-tab-group-chip in the clone.
            const clonedChip = clone.querySelector('.dv-tab-group-chip');
            const chipRect = chipEl.getBoundingClientRect();
            const cursorInChipX = event.clientX - chipRect.left;
            const cursorInChipY = event.clientY - chipRect.top;
            if (clonedChip) {
                const clonedChipRect = clonedChip.getBoundingClientRect();
                const wrapperRect = wrapper.getBoundingClientRect();
                const offsetX = clonedChipRect.left - wrapperRect.left + cursorInChipX;
                const offsetY = clonedChipRect.top - wrapperRect.top + cursorInChipY;
                event.dataTransfer.setDragImage(wrapper, offsetX, offsetY);
            }
            else {
                event.dataTransfer.setDragImage(wrapper, cursorInChipX, cursorInChipY);
            }
            // Clean up after the browser captures the image
            requestAnimationFrame(() => {
                wrapper.remove();
            });
        }
        cleanupTransition(panelId) {
            var _a;
            (_a = this._pendingTransitionCleanups.get(panelId)) === null || _a === void 0 ? void 0 : _a();
            this._pendingTransitionCleanups.delete(panelId);
        }
        updateDragAndDropState() {
            const caps = resolveDndCapabilities(this._ctx.accessor.options);
            for (const entry of this._chipRenderers.values()) {
                entry.chip.element.draggable = caps.html5;
                entry.html5DragSource.setDisabled(!caps.html5);
                entry.pointerDragSource.setDisabled(!caps.pointer);
                entry.pointerDragSource.setTouchOnly(!caps.pointerHandlesMouse);
            }
        }
        /**
         * Synchronously dispose the chip drag sources for an in-flight chip
         * drag. Called from `_commitGroupMove` so the transfer payload +
         * iframe shield are released BEFORE the cross-group move detaches
         * the chip (chip dispose is scheduled on a microtask via
         * `_scheduleTabGroupUpdate`, which is too late for callers that read
         * `getPanelData()` synchronously after the move). Idempotent — the
         * subsequent `update()` will also dispose the sources.
         */
        disposeChipDrag(tabGroupId) {
            var _a, _b;
            const entry = this._chipRenderers.get(tabGroupId);
            if (!entry) {
                return;
            }
            // Optional-chained because tests may inject minimal entries
            // that skip the manager's normal `_ensureChipForGroup` flow.
            (_a = entry.html5DragSource) === null || _a === void 0 ? void 0 : _a.dispose();
            (_b = entry.pointerDragSource) === null || _b === void 0 ? void 0 : _b.dispose();
        }
        /** Cloned chip rect used as the pointer follow-finger ghost. */
        _buildChipGhostElement(chipEl) {
            const style = getComputedStyle(chipEl);
            const clone = chipEl.cloneNode(true);
            Array.from(style).forEach((key) => {
                clone.style.setProperty(key, style.getPropertyValue(key), style.getPropertyPriority(key));
            });
            clone.style.position = 'absolute';
            return clone;
        }
        disposeAll() {
            var _a;
            (_a = this._indicator) === null || _a === void 0 ? void 0 : _a.dispose();
            this._indicator = null;
            for (const [, cleanup] of this._pendingTransitionCleanups) {
                cleanup();
            }
            this._pendingTransitionCleanups.clear();
            for (const [, entry] of this._chipRenderers) {
                entry.chip.element.remove();
                entry.chip.dispose();
                entry.disposable.dispose();
            }
            this._chipRenderers.clear();
        }
        _ensureIndicator() {
            var _a, _b;
            const mode = (_b = (_a = this._ctx.accessor.options.theme) === null || _a === void 0 ? void 0 : _a.tabGroupIndicator) !== null && _b !== void 0 ? _b : 'wrap';
            const Ctor = mode === 'none' ? NoneTabGroupIndicator : WrapTabGroupIndicator;
            // Re-create if the indicator type changed (e.g. theme switch)
            if (this._indicator && !(this._indicator instanceof Ctor)) {
                this._indicator.dispose();
                this._indicator = null;
            }
            if (!this._indicator) {
                this._indicator = new Ctor({
                    tabsList: this._ctx.tabsList,
                    getTabGroups: () => this._ctx.group.model.getTabGroups(),
                    getActivePanelId: () => { var _a; return (_a = this._ctx.group.activePanel) === null || _a === void 0 ? void 0 : _a.id; },
                    getTabMap: () => this._ctx.getTabMap(),
                    getChipElement: (id) => { var _a; return (_a = this._chipRenderers.get(id)) === null || _a === void 0 ? void 0 : _a.chip.element; },
                    getDirection: () => this._ctx.getDirection(),
                    getColorPalette: () => this._ctx.accessor.tabGroupColorPalette,
                });
            }
        }
        _ensureChipForGroup(tabGroup) {
            if (this._chipRenderers.has(tabGroup.id)) {
                return;
            }
            const createChip = this._ctx.accessor.options.createTabGroupChipComponent;
            const chip = createChip
                ? createChip(tabGroup)
                : new TabGroupChip(this._ctx.accessor.tabGroupColorPalette);
            chip.init({ tabGroup, api: this._ctx.accessor.api });
            const caps = resolveDndCapabilities(this._ctx.accessor.options);
            chip.element.draggable = caps.html5;
            const panelTransfer = LocalSelectionTransfer.getInstance();
            // Shared `getData` for both backends. Sets a group-level
            // PanelTransfer (panelId=null, tabGroupId identifies the group).
            // The returned disposer clears it on drag end.
            const getData = () => {
                panelTransfer.setData([
                    new PanelTransfer(this._ctx.accessor.id, this._ctx.group.id, null, tabGroup.id),
                ], PanelTransfer.prototype);
                return {
                    dispose: () => {
                        panelTransfer.clearData(PanelTransfer.prototype);
                    },
                };
            };
            // The chip's HTML5 drag image is the cloned tabs list (chip only),
            // mounted inside the dockview root for CSS-variable inheritance and
            // positioned against the chip's in-place rect. Layout-dependent
            // offset means we set the drag image directly in `onDragStart`
            // (inside the dragstart handler) rather than via the generic
            // `createGhost` factory, which only knows about ghost specs that
            // can be appended to `document.body`.
            const html5DragSource = html5Backend.createDragSource(chip.element, {
                getData,
                disabled: !caps.html5,
                isCancelled: () => !resolveDndCapabilities(this._ctx.accessor.options).html5,
                onDragStart: (event) => {
                    // Type guard via `dataTransfer` — `instanceof DragEvent`
                    // would throw in jsdom which doesn't ship a DragEvent
                    // constructor.
                    if ('dataTransfer' in event && event.dataTransfer) {
                        this.setGroupDragImage(event, tabGroup, chip.element);
                    }
                    this._callbacks.onChipDragStart(tabGroup, chip, event);
                },
                onDragEnd: (event) => {
                    var _a, _b;
                    (_b = (_a = this._callbacks).onChipDragEnd) === null || _b === void 0 ? void 0 : _b.call(_a, tabGroup, chip, event);
                },
            });
            // Synchronous panelTransfer cleanup directly on the chip element.
            // `Html5DragSource`'s dragend defers data disposal via `setTimeout(0)`
            // so drop handlers can read the payload — but a chip drag that
            // ends via `moveGroupOrPanel` (no actual drop event) needs the
            // singleton cleared immediately, otherwise a synchronous
            // `getPanelData()` after the move still sees the stale chip
            // payload. Attached directly (not via `addDisposableListener`) so
            // the listener survives chip disposal in the detach-then-dragend
            // cross-group path; `once: true` auto-removes after the single
            // dragend that we care about. (#1254)
            chip.element.addEventListener('dragend', () => {
                panelTransfer.clearData(PanelTransfer.prototype);
            }, { once: true });
            const pointerDragSource = pointerBackend.createDragSource(chip.element, {
                getData,
                disabled: !caps.pointer,
                touchOnly: !caps.pointerHandlesMouse,
                isCancelled: () => !resolveDndCapabilities(this._ctx.accessor.options).pointer,
                createGhost: () => ({
                    element: this._buildChipGhostElement(chip.element),
                    offsetX: 8,
                    offsetY: 8,
                }),
                onDragStart: (event) => {
                    this._callbacks.onChipDragStart(tabGroup, chip, event);
                },
            });
            const disposables = [
                tabGroup.onDidChange(() => {
                    var _a;
                    (_a = chip.update) === null || _a === void 0 ? void 0 : _a.call(chip, { tabGroup });
                    this._updateTabGroupClasses();
                }),
                tabGroup.onDidPanelChange(() => {
                    this._positionChipForGroup(tabGroup);
                    this._updateTabGroupClasses();
                }),
                tabGroup.onDidCollapseChange(() => {
                    this._updateTabGroupClasses();
                }),
                html5DragSource,
                pointerDragSource,
            ];
            // Context menu: built-in TabGroupChip already aggregates right-click
            // + touch long-press into `onContextMenu`. Custom chip renderers
            // don't, so attach a long-press detector and contextmenu listener
            // directly on their element.
            const onContextMenu = (event) => {
                // A long-press on a chip should preempt the in-flight pointer
                // drag and open the menu instead.
                pointerDragSource.cancelPending();
                this._callbacks.onChipContextMenu(tabGroup, event);
            };
            if (chip instanceof TabGroupChip) {
                disposables.push(chip.onContextMenu(onContextMenu));
            }
            else {
                disposables.push(new LongPressDetector(chip.element, {
                    onLongPress: onContextMenu,
                }), addDisposableListener(chip.element, 'contextmenu', onContextMenu));
            }
            // The chip sits before its group's first tab in the DOM, so it
            // covers the "drop before the group" position. Without a drop
            // target here, dropping a tab over the chip is a dead zone —
            // particularly visible when the group is first in the tabs list
            // and there's no preceding tab whose right zone covers position 0.
            // The smooth animation path already shifts the chip's margin to
            // open a gap, so suppress the overlay in that mode.
            const isVertical = this._ctx.getDirection() === 'vertical';
            const dropTarget = new Droptarget(chip.element, {
                acceptedTargetZones: isVertical ? ['top'] : ['left'],
                overlayModel: {
                    activationSize: { value: 100, type: 'percentage' },
                },
                canDisplayOverlay: (event, position) => {
                    var _a;
                    if (this._ctx.group.locked) {
                        return false;
                    }
                    if (this._ctx.accessor.options.disableDnd) {
                        return false;
                    }
                    const data = getPanelData();
                    if (data && this._ctx.accessor.id === data.viewId) {
                        if (((_a = this._ctx.accessor.options.theme) === null || _a === void 0 ? void 0 : _a.tabAnimation) ===
                            'smooth') {
                            return false;
                        }
                        return true;
                    }
                    return this._ctx.group.model.canDisplayOverlay(event, position, 'tab');
                },
            });
            disposables.push(dropTarget, dropTarget.onDrop((event) => {
                this._callbacks.onChipDrop(tabGroup, event);
            }));
            const disposable = new CompositeDisposable(...disposables);
            this._chipRenderers.set(tabGroup.id, {
                chip,
                html5DragSource,
                pointerDragSource,
                disposable,
                dropTarget,
            });
            // Group is born collapsed (cross-group drop, layout restore, etc.):
            // its tabs are about to be added without the collapsed class. Skip
            // the animation in the upcoming _updateTabGroupClasses call so they
            // apply the class instantly instead of transitioning from expanded.
            if (tabGroup.collapsed) {
                this._skipNextCollapseAnimation = true;
            }
        }
        _positionChipForGroup(tabGroup) {
            const entry = this._chipRenderers.get(tabGroup.id);
            if (!entry) {
                return;
            }
            const chipEl = entry.chip.element;
            const panelIds = tabGroup.panelIds;
            if (panelIds.length === 0) {
                chipEl.remove();
                return;
            }
            // Find the first tab element of this group
            const firstPanelId = panelIds[0];
            const firstTabEntry = this._ctx.getTabMap().get(firstPanelId);
            if (!firstTabEntry) {
                chipEl.remove();
                return;
            }
            // Insert chip before the first tab of the group
            const firstTabEl = firstTabEntry.value.element;
            if (chipEl.nextSibling !== firstTabEl) {
                this._ctx.tabsList.insertBefore(chipEl, firstTabEl);
            }
        }
        _updateTabGroupClasses() {
            var _a;
            const model = this._ctx.group.model;
            const tabGroups = model.getTabGroups();
            const tabs = this._ctx.getTabs();
            const tabMap = this._ctx.getTabMap();
            let hasAnimation = false;
            // Build a lookup: panelId → tabGroup
            const panelGroupMap = new Map();
            for (const tg of tabGroups) {
                for (const pid of tg.panelIds) {
                    panelGroupMap.set(pid, tg);
                }
            }
            for (const tabEntry of tabs) {
                const tab = tabEntry.value;
                const panelId = tab.panel.id;
                const tg = panelGroupMap.get(panelId);
                const isGrouped = !!tg;
                toggleClass(tab.element, 'dv-tab--grouped', isGrouped);
                if (tg) {
                    const ids = tg.panelIds;
                    const isFirst = ids[0] === panelId;
                    const isLast = ids[ids.length - 1] === panelId;
                    toggleClass(tab.element, 'dv-tab--group-first', isFirst);
                    toggleClass(tab.element, 'dv-tab--group-last', isLast);
                    // Expose the resolved group color as a CSS custom property
                    // so pure-CSS themes can use it for borders, backgrounds, etc.
                    applyTabGroupAccent(tab.element, tg.color, this._ctx.accessor.tabGroupColorPalette);
                    // Collapse / expand with animation
                    const isCollapsed = tab.element.classList.contains('dv-tab--group-collapsed');
                    if (!tg.collapsed && isCollapsed) {
                        // Collapsed → expanding: animate back
                        hasAnimation = true;
                        tab.element.classList.remove('dv-tab--group-collapsed');
                        tab.element.classList.add('dv-tab--group-expanding');
                        // Clean up any previous transitionend listener
                        // from a rapid collapse/expand cycle
                        (_a = this._pendingTransitionCleanups.get(panelId)) === null || _a === void 0 ? void 0 : _a();
                        const onEnd = () => {
                            tab.element.classList.remove('dv-tab--group-expanding');
                            tab.element.style.removeProperty('width');
                            tab.element.removeEventListener('transitionend', onEnd);
                            clearTimeout(fallbackTimer);
                            this._pendingTransitionCleanups.delete(panelId);
                        };
                        // Fallback in case transitionend never fires
                        // (e.g. element removed from DOM mid-transition)
                        const fallbackTimer = setTimeout(onEnd, 300);
                        this._pendingTransitionCleanups.set(panelId, onEnd);
                        tab.element.addEventListener('transitionend', onEnd);
                    }
                }
                else {
                    toggleClass(tab.element, 'dv-tab--group-first', false);
                    toggleClass(tab.element, 'dv-tab--group-last', false);
                    tab.element.classList.remove('dv-tab--group-collapsed', 'dv-tab--group-expanding');
                    tab.element.style.removeProperty('width');
                    tab.element.style.removeProperty('--dv-tab-group-color');
                }
            }
            // Track active group IDs for underline/collapse handling
            const activeGroupIds = new Set();
            // Handle collapse animation per group
            for (const tg of tabGroups) {
                activeGroupIds.add(tg.id);
                // Collapse animation
                const hasNewCollapse = tg.collapsed &&
                    tg.panelIds.some((pid) => {
                        const te = tabMap.get(pid);
                        return (te &&
                            !te.value.element.classList.contains('dv-tab--group-collapsed'));
                    });
                if (hasNewCollapse) {
                    if (this._skipNextCollapseAnimation) {
                        // Apply collapsed state instantly (no animation).
                        // Disable transitions so the CSS transition on
                        // dv-tab--group-collapsed doesn't fire.
                        const affected = [];
                        for (const pid of tg.panelIds) {
                            const te = tabMap.get(pid);
                            if (te) {
                                te.value.element.style.transition = 'none';
                                te.value.element.classList.add('dv-tab--group-collapsed');
                                affected.push(te.value.element);
                            }
                        }
                        if (affected.length > 0) {
                            void affected[0].offsetHeight; // single reflow
                            for (const el of affected) {
                                el.style.removeProperty('transition');
                            }
                        }
                    }
                    else {
                        hasAnimation = true;
                        const isVert = this._ctx.getDirection() === 'vertical';
                        for (const pid of tg.panelIds) {
                            const te = tabMap.get(pid);
                            if (te &&
                                !te.value.element.classList.contains('dv-tab--group-collapsed')) {
                                const rect = te.value.element.getBoundingClientRect();
                                if (isVert) {
                                    te.value.element.style.height = `${rect.height}px`;
                                }
                                else {
                                    te.value.element.style.width = `${rect.width}px`;
                                }
                                void te.value.element.offsetHeight; // force reflow
                                te.value.element.classList.add('dv-tab--group-collapsed');
                            }
                        }
                    }
                }
            }
            this._skipNextCollapseAnimation = false;
            // Sync indicator underlines and position them
            this._ensureIndicator();
            if (this._indicator) {
                this._indicator.syncUnderlineElements(activeGroupIds);
                if (hasAnimation) {
                    this._indicator.trackUnderlines();
                }
                else {
                    this._indicator.positionUnderlines();
                }
            }
        }
    }

    class Tabs extends CompositeDisposable {
        get showTabsOverflowControl() {
            return this._showTabsOverflowControl;
        }
        set showTabsOverflowControl(value) {
            if (this._showTabsOverflowControl == value) {
                return;
            }
            this._showTabsOverflowControl = value;
            if (value) {
                const observer = new OverflowObserver(this._tabsList);
                this._observerDisposable.value = new CompositeDisposable(observer, observer.onDidChange((event) => {
                    const hasOverflow = event.hasScrollX || event.hasScrollY;
                    this.toggleDropdown({ reset: !hasOverflow });
                    if (this._tabGroupManager.groupUnderlines.size > 0) {
                        this._tabGroupManager.positionUnderlines();
                    }
                }), addDisposableListener(this._tabsList, 'scroll', () => {
                    this.toggleDropdown({ reset: false });
                    if (this._tabGroupManager.groupUnderlines.size > 0) {
                        this._tabGroupManager.positionUnderlines();
                    }
                }));
            }
        }
        get element() {
            return this._element;
        }
        set voidContainer(el) {
            var _a;
            (_a = this._voidContainerListeners) === null || _a === void 0 ? void 0 : _a.dispose();
            this._voidContainerListeners = null;
            this._voidContainer = el;
            if (el) {
                this._voidContainerListeners = new CompositeDisposable(addDisposableListener(el, 'dragover', (event) => {
                    if (this._animState) {
                        event.preventDefault();
                    }
                }), addDisposableListener(el, 'drop', (event) => {
                    var _a;
                    if (((_a = this._animState) === null || _a === void 0 ? void 0 : _a.sourceTabGroupId) &&
                        this._animState.currentInsertionIndex !== null) {
                        event.preventDefault();
                        event.stopPropagation();
                        this.handleVoidDrop();
                    }
                }));
            }
        }
        /**
         * Handle a drop that occurred on the void container (empty header
         * space to the right of the tabs). Returns `true` if the drop was
         * consumed by an active group drag, `false` otherwise.
         */
        handleVoidDrop() {
            var _a, _b;
            if (!((_a = this._animState) === null || _a === void 0 ? void 0 : _a.sourceTabGroupId)) {
                return false;
            }
            const sourceTabGroupId = this._animState.sourceTabGroupId;
            const insertionIndex = (_b = this._animState.currentInsertionIndex) !== null && _b !== void 0 ? _b : this._tabs.length;
            this._animState = null;
            this._commitGroupMove(sourceTabGroupId, insertionIndex);
            return true;
        }
        get panels() {
            return this._tabs.map((_) => _.value.panel.id);
        }
        get size() {
            return this._tabs.length;
        }
        get tabs() {
            return this._tabs.map((_) => _.value);
        }
        get direction() {
            return this._direction;
        }
        set direction(value) {
            if (this._direction === value) {
                return;
            }
            this._direction = value;
            if (this._scrollbar) {
                this._scrollbar.orientation = value;
            }
            removeClasses(this._tabsList, 'dv-horizontal', 'dv-vertical');
            if (value === 'vertical') {
                addClasses(this._tabsList, 'dv-tabs-container-vertical', 'dv-vertical');
            }
            else {
                removeClasses(this._tabsList, 'dv-tabs-container-vertical');
                addClasses(this._tabsList, 'dv-horizontal');
            }
            for (const tab of this._tabs) {
                tab.value.setDirection(value);
            }
            this._tabGroupManager.updateDirection();
        }
        constructor(group, accessor, options) {
            super();
            this.group = group;
            this.accessor = accessor;
            this._observerDisposable = new MutableDisposable();
            this._scrollbar = null;
            this._tabs = [];
            this._tabMap = new Map();
            this.selectedIndex = -1;
            this._showTabsOverflowControl = false;
            this._direction = 'horizontal';
            this._animState = null;
            this._pendingMarginCleanups = new Map();
            this._pendingCollapse = false;
            this._flipTransitionCleanup = null;
            this._voidContainer = null;
            this._voidContainerListeners = null;
            this._extendedDropZone = null;
            this._pointerInsideTabsList = false;
            this._onTabDragStart = new Emitter();
            this.onTabDragStart = this._onTabDragStart.event;
            this._onDrop = new Emitter();
            this.onDrop = this._onDrop.event;
            this._onWillShowOverlay = new Emitter();
            this.onWillShowOverlay = this._onWillShowOverlay.event;
            this._onOverflowTabsChange = new Emitter();
            this.onOverflowTabsChange = this._onOverflowTabsChange.event;
            this._tabsList = document.createElement('div');
            this._tabsList.className = 'dv-tabs-container';
            this.showTabsOverflowControl = options.showTabsOverflowControl;
            if (accessor.options.scrollbars === 'native') {
                this._element = this._tabsList;
            }
            else {
                this._scrollbar = new Scrollbar(this._tabsList);
                this._scrollbar.orientation = this.direction;
                this._element = this._scrollbar.element;
                this.addDisposables(this._scrollbar);
            }
            this._tabGroupManager = new TabGroupManager({
                group: this.group,
                accessor: this.accessor,
                tabsList: this._tabsList,
                getTabs: () => this._tabs,
                getTabMap: () => this._tabMap,
                getDirection: () => this._direction,
            }, {
                onChipContextMenu: (tabGroup, event) => {
                    this.accessor.contextMenuController.showForChip(tabGroup, this.group, event);
                },
                onChipDragStart: (tabGroup, chip, event) => {
                    this._handleChipDragStart(tabGroup, chip, event);
                },
                onChipDragEnd: () => {
                    // HTML5 chip dragend (incl. cancels). The Html5DragSource
                    // owns the listener on the chip element, so this fires
                    // even if the chip was detached cross-group — the
                    // element keeps its listeners until the source is
                    // disposed. resetDragAnimation is a no-op after a
                    // successful drop (anim state already null) thanks to
                    // the gating inside it.
                    this.resetDragAnimation();
                },
                onChipDrop: (tabGroup, event) => {
                    this._handleChipDrop(tabGroup, event);
                },
            });
            this.addDisposables(this._onOverflowTabsChange, this._observerDisposable, this._onWillShowOverlay, this._onDrop, this._onTabDragStart, {
                dispose: () => {
                    var _a;
                    (_a = this._flipTransitionCleanup) === null || _a === void 0 ? void 0 : _a.call(this);
                },
            },
            // Pointer-side cleanup: when any pointer drag ends, tear
            // down smooth-reorder anim state the dragover bridge may
            // have installed. The chip's pointer drag source handles
            // its own transfer payload + iframe-shield cleanup.
            PointerDragController.getInstance().onDragEnd(() => {
                this._pointerInsideTabsList = false;
                this.resetDragAnimation();
            }),
            // Pointer-event mirror of the HTML5 dragover / dragleave handlers
            // below. Drives smooth-reorder for `dndStrategy: 'pointer'` and
            // for touch drags in `'auto'`.
            PointerDragController.getInstance().onDragMove((e) => {
                this._handlePointerDragMove(e.clientX, e.clientY);
            }), addDisposableListener(this.element, 'pointerdown', (event) => {
                if (event.defaultPrevented) {
                    return;
                }
                const isLeftClick = event.button === 0;
                if (isLeftClick) {
                    this.accessor.doSetGroupActive(this.group);
                }
            }),
            // Trackpad / wheel forwarding. The strip scrolls along its own
            // axis (x for horizontal headers, y for vertical), so deltaY
            // from a plain mouse wheel maps onto the strip's axis too —
            // this gives the VS Code-style "scroll over tab bar to page
            // through tabs" feel. We only consume the event when the strip
            // is actually overflowing in the direction the user wheeled in,
            // so a wheel at the edge of a non-overflowing strip still
            // bubbles up and scrolls the page. `{ passive: false }` is
            // required because we call preventDefault().
            addDisposableListener(this._tabsList, 'wheel', (event) => {
                const isVertical = this._direction === 'vertical';
                const primary = isVertical
                    ? event.deltaY || event.deltaX
                    : event.deltaX || event.deltaY;
                if (primary === 0) {
                    return;
                }
                const max = isVertical
                    ? this._tabsList.scrollHeight -
                        this._tabsList.clientHeight
                    : this._tabsList.scrollWidth -
                        this._tabsList.clientWidth;
                if (max <= 0) {
                    return;
                }
                const current = isVertical
                    ? this._tabsList.scrollTop
                    : this._tabsList.scrollLeft;
                // At the edge in the wheel direction: let the page
                // scroll instead of trapping the gesture.
                if ((primary < 0 && current <= 0) ||
                    (primary > 0 && current >= max)) {
                    return;
                }
                event.preventDefault();
                // Custom-scrollbar mode wraps the tabs list and installs
                // its own wheel listener that rewrites scrollLeft from a
                // deltaY-only tracker. Without stopPropagation that
                // handler would clobber our deltaX-aware update.
                event.stopPropagation();
                if (isVertical) {
                    this._tabsList.scrollTop = current + primary;
                }
                else {
                    this._tabsList.scrollLeft = current + primary;
                }
            }, { passive: false }), addDisposableListener(this._tabsList, 'dragover', (event) => {
                if (this._processDragOver(event.clientX)) {
                    // Allow `drop` to fire on the tabs list container.
                    event.preventDefault();
                }
            }, true), addDisposableListener(this._tabsList, 'dragleave', (event) => {
                this._processDragLeave(event.relatedTarget);
            }, true), addDisposableListener(this._tabsList, 'dragend', () => {
                this.resetDragAnimation();
            }), addDisposableListener(this._tabsList, 'drop', (event) => {
                var _a, _b, _c;
                if (!this._animState ||
                    this._animState.currentInsertionIndex === null) {
                    return;
                }
                // In non-smooth mode only handle group drags here;
                // individual tab drops are handled by tab Droptargets.
                if (((_a = this.accessor.options.theme) === null || _a === void 0 ? void 0 : _a.tabAnimation) !==
                    'smooth' &&
                    !this._animState.sourceTabGroupId) {
                    return;
                }
                event.stopPropagation();
                event.preventDefault();
                // The capturing stopPropagation above prevents the
                // individual tab's Droptarget.onDrop from firing, so
                // the anchor overlay won't be cleared by that path.
                // Clear it explicitly here before processing the drop.
                (_c = (_b = this.group.model.dropTargetContainer) === null || _b === void 0 ? void 0 : _b.model) === null || _c === void 0 ? void 0 : _c.clear();
                const animState = this._animState;
                this._animState = null;
                this._pendingCollapse = false;
                // Handle group drag (entire group repositioned)
                if (animState.sourceTabGroupId) {
                    this._commitGroupMove(animState.sourceTabGroupId, animState.currentInsertionIndex);
                    return;
                }
                const insertionIndex = animState.currentInsertionIndex;
                const sourceIndex = animState.sourceIndex;
                const adjustedIndex = insertionIndex -
                    (sourceIndex !== -1 && sourceIndex < insertionIndex
                        ? 1
                        : 0);
                const sourceCurrentGroup = this.group.model.getTabGroupForPanel(animState.sourceTabId);
                if (adjustedIndex === sourceIndex &&
                    !animState.targetTabGroupId &&
                    !sourceCurrentGroup) {
                    this._uncollapsSourceTab(animState.sourceTabId);
                    this.resetTabTransforms();
                    return;
                }
                this._uncollapsSourceTab(animState.sourceTabId);
                const firstPositions = this.snapshotTabPositions();
                this.resetTabTransforms();
                this._onDrop.fire({
                    event,
                    index: adjustedIndex,
                    targetTabGroupId: animState.targetTabGroupId,
                });
                this.runFlipAnimation(firstPositions, animState.sourceTabId, animState.sourceIndex === -1, {
                    from: Math.min(sourceIndex, adjustedIndex),
                    to: Math.max(sourceIndex, adjustedIndex),
                });
            }, true), exports.DockviewDisposable.from(() => {
                var _a;
                (_a = this._voidContainerListeners) === null || _a === void 0 ? void 0 : _a.dispose();
                this.resetDragAnimation();
                this._tabGroupManager.disposeAll();
                for (const { value, disposable } of this._tabs) {
                    disposable.dispose();
                    value.dispose();
                }
                this._tabs = [];
                this._tabMap.clear();
            }));
        }
        indexOf(id) {
            return this._tabs.findIndex((tab) => tab.value.panel.id === id);
        }
        isActive(tab) {
            return (this.selectedIndex > -1 &&
                this._tabs[this.selectedIndex].value === tab);
        }
        setActivePanel(panel) {
            const isVertical = this._direction === 'vertical';
            let running = 0;
            for (const tab of this._tabs) {
                const isActivePanel = panel.id === tab.value.panel.id;
                tab.value.setActive(isActivePanel);
                if (isActivePanel) {
                    const element = tab.value.element;
                    const parentElement = element.parentElement;
                    if (isVertical) {
                        if (running < parentElement.scrollTop ||
                            running + element.clientHeight >
                                parentElement.scrollTop + parentElement.clientHeight) {
                            parentElement.scrollTop = running;
                        }
                    }
                    else {
                        if (running < parentElement.scrollLeft ||
                            running + element.clientWidth >
                                parentElement.scrollLeft + parentElement.clientWidth) {
                            parentElement.scrollLeft = running;
                        }
                    }
                }
                running += isVertical
                    ? tab.value.element.clientHeight
                    : tab.value.element.clientWidth;
            }
            // Reposition underlines so the wrap-around follows the new active tab
            if (this._tabGroupManager.groupUnderlines.size > 0) {
                this._tabGroupManager.positionUnderlines();
            }
        }
        openPanel(panel, index = this._tabs.length) {
            if (this._tabMap.has(panel.id)) {
                return;
            }
            const tab = new Tab(panel, this.accessor, this.group);
            tab.setContent(panel.view.tab);
            if (this._direction !== 'horizontal') {
                tab.setDirection(this._direction);
            }
            const disposable = new CompositeDisposable(tab.onDragStart((event) => {
                var _a;
                this._onTabDragStart.fire({ nativeEvent: event, panel });
                // Both HTML5 and pointer drags initialize _animState. Cleanup
                // is wired in both paths: HTML5 via dragend/drop on _tabsList,
                // pointer via PointerDragController.onDragEnd subscriptions.
                if (((_a = this.accessor.options.theme) === null || _a === void 0 ? void 0 : _a.tabAnimation) === 'smooth') {
                    const tabWidth = tab.element.getBoundingClientRect().width;
                    const sourceIndex = this._tabs.findIndex((x) => x.value === tab);
                    this._animState = {
                        sourceTabId: panel.id,
                        sourceIndex,
                        tabPositions: this.snapshotTabPositions(),
                        chipPositions: this._tabGroupManager.snapshotChipWidths(),
                        currentInsertionIndex: null,
                        targetTabGroupId: null,
                        sourceTabGroupId: null,
                        sourceGroupPanelIds: null,
                        sourceChipWidth: 0,
                        cursorOffsetFromDragLeft: tabWidth / 2,
                        sourceGapWidth: tabWidth,
                        containerLeft: this._tabsList.getBoundingClientRect().left,
                    };
                    // Collapse the source tab after the browser captures the
                    // drag image, then open the gap at the source position in
                    // the same paint frame — no visual jump.
                    // Both collapse and gap must be instant (no transition).
                    this._pendingCollapse = true;
                    requestAnimationFrame(() => {
                        var _a;
                        var _b;
                        this._pendingCollapse = false;
                        if (!this._animState) {
                            return;
                        }
                        // Collapse source tab instantly (no transition)
                        tab.element.style.transition = 'none';
                        toggleClass(tab.element, 'dv-tab--dragging', true);
                        void tab.element.offsetHeight; // force reflow
                        (_a = (_b = this._animState).currentInsertionIndex) !== null && _a !== void 0 ? _a : (_b.currentInsertionIndex = sourceIndex);
                        // Apply gap with transitions disabled on the target
                        this.applyDragOverTransforms(true);
                        // Re-enable transitions for subsequent moves
                        tab.element.style.removeProperty('transition');
                    });
                }
            }), tab.onTabClick((event) => {
                if (event.defaultPrevented) {
                    return;
                }
                if (this.group.api.location.type !== 'edge') {
                    return;
                }
                if (this.group.activePanel === panel) {
                    // Clicking the active tab toggles expansion
                    if (this.group.api.isCollapsed()) {
                        this.group.api.expand();
                    }
                    else {
                        this.group.api.collapse();
                    }
                }
                else {
                    // Clicking a non-active tab switches the active tab.
                    // If the group is collapsed, also expand it.
                    this.group.model.openPanel(panel);
                    if (this.group.api.isCollapsed()) {
                        this.group.api.expand();
                    }
                }
            }), tab.onPointerDown((event) => {
                if (event.defaultPrevented) {
                    return;
                }
                const isFloatingGroupsEnabled = !this.accessor.options.disableFloatingGroups;
                const isFloatingWithOnePanel = this.group.api.location.type === 'floating' &&
                    this.size === 1;
                if (isFloatingGroupsEnabled &&
                    !isFloatingWithOnePanel &&
                    event.shiftKey) {
                    event.preventDefault();
                    const panel = this.accessor.getGroupPanel(tab.panel.id);
                    const { top, left } = tab.element.getBoundingClientRect();
                    const { top: rootTop, left: rootLeft } = this.accessor.element.getBoundingClientRect();
                    this.accessor.addFloatingGroup(panel, {
                        x: left - rootLeft,
                        y: top - rootTop,
                        inDragMode: true,
                    });
                    return;
                }
                switch (event.button) {
                    case 0:
                        if (this.group.api.location.type === 'edge') ;
                        else {
                            if (this.group.activePanel !== panel) {
                                this.group.model.openPanel(panel);
                            }
                        }
                        break;
                }
            }), tab.onDrop((event) => {
                var _a, _b, _c, _d;
                const animState = this._animState;
                this._animState = null;
                this._pendingCollapse = false;
                const tabIndex = this._tabs.findIndex((x) => x.value === tab);
                if (animState) {
                    const dropIndex = event.position === 'right' ? tabIndex + 1 : tabIndex;
                    if (animState.sourceTabGroupId) {
                        this._commitGroupMove(animState.sourceTabGroupId, (_a = animState.currentInsertionIndex) !== null && _a !== void 0 ? _a : dropIndex);
                        return;
                    }
                    this._uncollapsSourceTab(animState.sourceTabId);
                    const firstPositions = this.snapshotTabPositions();
                    this.resetTabTransforms();
                    this._onDrop.fire({
                        event: event.nativeEvent,
                        index: dropIndex,
                        targetTabGroupId: animState.targetTabGroupId,
                    });
                    if (((_b = this.accessor.options.theme) === null || _b === void 0 ? void 0 : _b.tabAnimation) === 'smooth') {
                        this.runFlipAnimation(firstPositions, animState.sourceTabId, animState.sourceIndex === -1, animState.sourceIndex !== -1
                            ? {
                                from: Math.min(animState.sourceIndex, dropIndex),
                                to: Math.max(animState.sourceIndex, dropIndex),
                            }
                            : undefined);
                    }
                }
                else {
                    // Compute insertion index based on which half of the tab
                    // the pointer is over, then adjust for same-group removal:
                    // when the source tab sits before the insertion point,
                    // removing it shifts all subsequent indices down by one.
                    const afterPosition = this._direction === 'vertical' ? 'bottom' : 'right';
                    const insertionIndex = event.position === afterPosition
                        ? tabIndex + 1
                        : tabIndex;
                    const data = getPanelData();
                    const sourceIndex = data
                        ? this._tabs.findIndex((x) => x.value.panel.id === data.panelId)
                        : -1;
                    const adjustedIndex = insertionIndex -
                        (sourceIndex !== -1 && sourceIndex < insertionIndex
                            ? 1
                            : 0);
                    const targetTabGroupId = (_d = (_c = this.group.model.getTabGroupForPanel(tab.panel.id)) === null || _c === void 0 ? void 0 : _c.id) !== null && _d !== void 0 ? _d : null;
                    this._onDrop.fire({
                        event: event.nativeEvent,
                        index: adjustedIndex,
                        targetTabGroupId,
                    });
                }
            }), tab.onWillShowOverlay((event) => {
                this._onWillShowOverlay.fire(new DockviewWillShowOverlayLocationEvent(event, {
                    kind: 'tab',
                    panel: this.group.activePanel,
                    api: this.accessor.api,
                    group: this.group,
                    getData: getPanelData,
                }));
            }));
            const value = { value: tab, disposable };
            this.addTab(value, index);
            // A new tab may have been inserted between a chip and its
            // group's first tab — reposition all chips to stay correct.
            this._tabGroupManager.positionAllChips();
            // If a tab was added during active drag, refresh positions
            if (this._animState) {
                this._animState.tabPositions = this.snapshotTabPositions();
                this._animState.chipPositions =
                    this._tabGroupManager.snapshotChipWidths();
                this.applyDragOverTransforms();
            }
        }
        delete(id) {
            var _a;
            if (((_a = this._animState) === null || _a === void 0 ? void 0 : _a.sourceTabId) === id) {
                this.resetTabTransforms();
                this._animState = null;
            }
            // Force-clean any pending transitionend listener
            this._tabGroupManager.cleanupTransition(id);
            const index = this.indexOf(id);
            const tabToRemove = this._tabs.splice(index, 1)[0];
            this._tabMap.delete(id);
            if (tabToRemove) {
                const { value, disposable } = tabToRemove;
                disposable.dispose();
                value.dispose();
                value.element.remove();
            }
            // If a non-source tab was removed during active drag, refresh positions
            if (this._animState) {
                this._animState.tabPositions = this.snapshotTabPositions();
                this._animState.chipPositions =
                    this._tabGroupManager.snapshotChipWidths();
                this.applyDragOverTransforms();
            }
        }
        addTab(tab, index = this._tabs.length) {
            if (index < 0 || index > this._tabs.length) {
                throw new Error('invalid location');
            }
            // Use the tab element at `index` as the reference node rather than
            // `children[index]`, because `_tabsList` may contain non-tab children
            // (e.g. group chips, underlines) that shift the DOM indices.
            const refNode = index < this._tabs.length ? this._tabs[index].value.element : null;
            this._tabsList.insertBefore(tab.value.element, refNode);
            this._tabs = [
                ...this._tabs.slice(0, index),
                tab,
                ...this._tabs.slice(index),
            ];
            this._tabMap.set(tab.value.panel.id, tab);
            if (this.selectedIndex < 0) {
                this.selectedIndex = index;
            }
        }
        toggleDropdown(options) {
            if (options.reset) {
                this._onOverflowTabsChange.fire({
                    tabs: [],
                    tabGroups: [],
                    reset: true,
                });
                return;
            }
            const tabs = this._tabs
                .filter((tab) => !isChildEntirelyVisibleWithinParent(tab.value.element, this._tabsList))
                .map((x) => x.value.panel.id);
            // Detect tab groups whose chip is clipped or whose tabs are all
            // in the overflow set (e.g. collapsed groups scrolled out of view).
            const overflowTabSet = new Set(tabs);
            const tabGroups = [];
            for (const tg of this.group.model.getTabGroups()) {
                const chipEntry = this._tabGroupManager.chipRenderers.get(tg.id);
                const chipClipped = chipEntry &&
                    !isChildEntirelyVisibleWithinParent(chipEntry.chip.element, this._tabsList);
                // A group is in overflow if its chip is clipped OR all its
                // visible tabs are in the overflow set.
                const allTabsOverflow = tg.panelIds.length > 0 &&
                    tg.panelIds.every((pid) => overflowTabSet.has(pid));
                if (chipClipped || allTabsOverflow) {
                    tabGroups.push(tg.id);
                    // For collapsed groups whose chip is clipped, ensure all
                    // member tabs are included in the overflow list so they
                    // appear in the dropdown.
                    if (tg.collapsed) {
                        for (const pid of tg.panelIds) {
                            if (!overflowTabSet.has(pid)) {
                                overflowTabSet.add(pid);
                                tabs.push(pid);
                            }
                        }
                    }
                }
            }
            this._onOverflowTabsChange.fire({ tabs, tabGroups, reset: false });
        }
        updateDragAndDropState() {
            for (const tab of this._tabs) {
                tab.value.updateDragAndDropState();
            }
            this._tabGroupManager.updateDragAndDropState();
        }
        /**
         * Synchronize chip elements and CSS classes for all tab groups
         * in the parent group model. Call after any tab group mutation.
         */
        updateTabGroups() {
            this._tabGroupManager.update();
        }
        refreshTabGroupAccent() {
            this._tabGroupManager.refreshAccents();
        }
        /**
         * Tabs-list-specific side effects of a chip drag start. The chip's
         * drag sources (constructed by `TabGroupManager`) own the transfer
         * payload, iframe shielding, dataTransfer setup, and the HTML5 drag
         * image. This method just sets up the smooth-reorder anim state and
         * collapses the source-group tabs in the tabs list.
         */
        _handleChipDragStart(tabGroup, chip, event) {
            var _a;
            const firstPanelId = tabGroup.panelIds[0];
            const firstIdx = firstPanelId
                ? this._tabs.findIndex((t) => t.value.panel.id === firstPanelId)
                : -1;
            const chipRect = chip.element.getBoundingClientRect();
            // Compute total group width (chip + all tabs)
            let groupGapWidth = chipRect.width;
            for (const pid of tabGroup.panelIds) {
                const tabEntry = this._tabMap.get(pid);
                if (tabEntry) {
                    groupGapWidth +=
                        tabEntry.value.element.getBoundingClientRect().width;
                }
            }
            this._animState = {
                sourceTabId: '',
                sourceIndex: firstIdx,
                tabPositions: this.snapshotTabPositions(),
                chipPositions: this._tabGroupManager.snapshotChipWidths(),
                currentInsertionIndex: null,
                targetTabGroupId: null,
                sourceTabGroupId: tabGroup.id,
                sourceGroupPanelIds: new Set(tabGroup.panelIds),
                sourceChipWidth: chipRect.width,
                cursorOffsetFromDragLeft: event.clientX - chipRect.left,
                sourceGapWidth: groupGapWidth,
                containerLeft: this._tabsList.getBoundingClientRect().left,
            };
            if (((_a = this.accessor.options.theme) === null || _a === void 0 ? void 0 : _a.tabAnimation) !== 'smooth') {
                return;
            }
            // Collapse group tabs + chip after the browser captures the drag
            // image, then open the gap at the source position — all instant
            // (no transitions).
            const groupPanelIds = new Set(tabGroup.panelIds);
            this._pendingCollapse = true;
            requestAnimationFrame(() => {
                var _a;
                var _b;
                this._pendingCollapse = false;
                if (!this._animState) {
                    return;
                }
                // Collapse all group tabs instantly
                for (const t of this._tabs) {
                    if (groupPanelIds.has(t.value.panel.id)) {
                        t.value.element.style.transition = 'none';
                        toggleClass(t.value.element, 'dv-tab--dragging', true);
                    }
                }
                // Collapse the group chip instantly
                const chipEntry = this._tabGroupManager.chipRenderers.get(tabGroup.id);
                if (chipEntry) {
                    chipEntry.chip.element.style.transition = 'none';
                    toggleClass(chipEntry.chip.element, 'dv-tab-group-chip--dragging', true);
                }
                // Single reflow for the entire batch
                void this._tabsList.offsetHeight;
                const underline = this._tabGroupManager.groupUnderlines.get(tabGroup.id);
                if (underline) {
                    underline.style.display = 'none';
                }
                (_a = (_b = this._animState).currentInsertionIndex) !== null && _a !== void 0 ? _a : (_b.currentInsertionIndex = firstIdx);
                this.applyDragOverTransforms(true);
                for (const t of this._tabs) {
                    if (groupPanelIds.has(t.value.panel.id)) {
                        t.value.element.style.removeProperty('transition');
                    }
                }
                if (chipEntry) {
                    chipEntry.chip.element.style.removeProperty('transition');
                }
            });
        }
        /**
         * A drop on a tab group chip means "insert before this group". Resolve to
         * the index of the group's first tab, adjusting for same-group removal
         * (when the source tab is currently to the left of the target slot, its
         * removal shifts the insertion index down by one). Always clears
         * `targetTabGroupId` so the dropped tab lands outside the group.
         */
        _handleChipDrop(tabGroup, event) {
            const firstPanelId = tabGroup.panelIds[0];
            if (!firstPanelId) {
                return;
            }
            const insertionIndex = this._tabs.findIndex((x) => x.value.panel.id === firstPanelId);
            if (insertionIndex === -1) {
                return;
            }
            const data = getPanelData();
            const sourceIndex = data && data.groupId === this.group.id && data.panelId
                ? this._tabs.findIndex((x) => x.value.panel.id === data.panelId)
                : -1;
            const adjustedIndex = insertionIndex -
                (sourceIndex !== -1 && sourceIndex < insertionIndex ? 1 : 0);
            this._onDrop.fire({
                event: event.nativeEvent,
                index: adjustedIndex,
                targetTabGroupId: null,
            });
        }
        /**
         * Sets the broader container that is part of the same logical drop surface
         * as this tab list (e.g. the full header element).  When a dragleave from
         * the tabs list lands inside this container, `_animState` is preserved so
         * that external dragover listeners can continue the animation.
         */
        setExtendedDropZone(el) {
            this._extendedDropZone = el;
        }
        /**
         * Allows external elements (e.g. void container, left actions) to push an
         * insertion index into the animation while the cursor is outside the tabs
         * list itself.  Pass `null` to clear the indicator.
         */
        setExternalInsertionIndex(index) {
            if (!this._animState) {
                return;
            }
            if (index === this._animState.currentInsertionIndex) {
                return;
            }
            this._animState.currentInsertionIndex = index;
            this.applyDragOverTransforms();
        }
        /**
         * Called when the drag cursor leaves the entire header area (not just the
         * tabs list).  Clears animation state for cross-group drags, which never
         * receive a `dragend` event on this tab list.
         */
        clearExternalAnimState() {
            if (!this._animState) {
                return;
            }
            this.resetTabTransforms();
            if (this._animState.sourceIndex === -1) {
                this._animState = null;
            }
            else {
                this._animState.currentInsertionIndex = null;
            }
        }
        snapshotTabPositions() {
            const positions = new Map();
            for (const tab of this._tabs) {
                positions.set(tab.value.panel.id, tab.value.element.getBoundingClientRect());
            }
            return positions;
        }
        getAverageTabWidth() {
            if (this._tabs.length === 0) {
                return 0;
            }
            const isVertical = this._direction === 'vertical';
            let total = 0;
            for (const tab of this._tabs) {
                const rect = tab.value.element.getBoundingClientRect();
                total += isVertical ? rect.height : rect.width;
            }
            return total / this._tabs.length;
        }
        /**
         * Pointer-event entry point. The HTML5 path enters via the per-element
         * `dragover` listener; this one hit-tests the global pointer-drag
         * position against the tabs list and routes through the same shared
         * `_processDragOver` / `_processDragLeave` helpers.
         */
        _handlePointerDragMove(clientX, clientY) {
            var _a;
            const sourceDoc = (_a = this._tabsList.ownerDocument) !== null && _a !== void 0 ? _a : document;
            const elAtPoint = sourceDoc.elementFromPoint(clientX, clientY);
            const inside = !!elAtPoint &&
                (this._tabsList.contains(elAtPoint) ||
                    (!!this._extendedDropZone &&
                        this._extendedDropZone.contains(elAtPoint)));
            if (!inside) {
                if (this._pointerInsideTabsList) {
                    this._pointerInsideTabsList = false;
                    this._processDragLeave(elAtPoint);
                }
                return;
            }
            this._pointerInsideTabsList = true;
            this._processDragOver(clientX);
        }
        /**
         * Shared body of the dragover entry point. Refreshes stale anim state
         * for a changed drag identity, initializes anim state for incoming
         * cross-group drags, and dispatches to the gap-following math in
         * `handleDragOver`. Returns true when this tabs list has taken
         * ownership of the drag — HTML5 callers use this to gate
         * `event.preventDefault()`.
         */
        _processDragOver(clientX) {
            var _a, _b, _c, _d;
            if (this.accessor.options.disableDnd) {
                return false;
            }
            // Stale-state guard: if a previous drag's anim state is still here
            // but the current drag is a different identity, drop the stale one
            // so the new drag starts from a clean slate.
            if (this._animState) {
                const data = getPanelData();
                if ((data === null || data === void 0 ? void 0 : data.tabGroupId) &&
                    data.groupId !== this.group.id &&
                    this._animState.sourceTabGroupId !== data.tabGroupId) {
                    this._animState = null;
                }
            }
            if (!this._animState) {
                const data = getPanelData();
                // In default animation mode, individual tab drops are handled
                // by per-tab Droptargets; only chip drags need tabs-list-level
                // handling so drops on void space still work.
                if (((_a = this.accessor.options.theme) === null || _a === void 0 ? void 0 : _a.tabAnimation) === 'default' &&
                    !(data === null || data === void 0 ? void 0 : data.tabGroupId)) {
                    return false;
                }
                if (data &&
                    (data.panelId || data.tabGroupId) &&
                    data.groupId !== this.group.id) {
                    const avgWidth = this.getAverageTabWidth();
                    if (data.tabGroupId) {
                        // External group drag — look up the source group to
                        // size the gap.
                        const sourceGroup = this.accessor.getPanel(data.groupId);
                        const sourceTg = sourceGroup === null || sourceGroup === void 0 ? void 0 : sourceGroup.model.getTabGroups().find((tg) => tg.id === data.tabGroupId);
                        const panelCount = (_b = sourceTg === null || sourceTg === void 0 ? void 0 : sourceTg.panelIds.length) !== null && _b !== void 0 ? _b : 1;
                        const groupGapWidth = avgWidth * panelCount + avgWidth;
                        this._animState = {
                            sourceTabId: '',
                            sourceIndex: -1,
                            tabPositions: this.snapshotTabPositions(),
                            chipPositions: this._tabGroupManager.snapshotChipWidths(),
                            currentInsertionIndex: null,
                            targetTabGroupId: null,
                            sourceTabGroupId: data.tabGroupId,
                            sourceGroupPanelIds: sourceTg
                                ? new Set(sourceTg.panelIds)
                                : new Set(),
                            sourceChipWidth: avgWidth,
                            cursorOffsetFromDragLeft: groupGapWidth / 2,
                            sourceGapWidth: groupGapWidth,
                            containerLeft: this._tabsList.getBoundingClientRect().left,
                        };
                    }
                    else {
                        this._animState = {
                            sourceTabId: data.panelId,
                            sourceIndex: -1,
                            tabPositions: this.snapshotTabPositions(),
                            chipPositions: this._tabGroupManager.snapshotChipWidths(),
                            currentInsertionIndex: null,
                            targetTabGroupId: null,
                            sourceTabGroupId: null,
                            sourceGroupPanelIds: null,
                            sourceChipWidth: 0,
                            cursorOffsetFromDragLeft: avgWidth / 2,
                            sourceGapWidth: avgWidth,
                            containerLeft: this._tabsList.getBoundingClientRect().left,
                        };
                    }
                }
                else {
                    return false;
                }
            }
            // For intra-group drag (sourceIndex >= 0) the gap animation is the
            // sole visual indicator — clear any stale anchor overlay that may
            // have been set while the cursor was over the panel content area or
            // another zone. External drags (sourceIndex === -1) leave the
            // overlay to the individual tab Droptargets so cross-group
            // animation is not disrupted.
            if (this._animState.sourceIndex !== -1) {
                (_d = (_c = this.group.model.dropTargetContainer) === null || _c === void 0 ? void 0 : _c.model) === null || _d === void 0 ? void 0 : _d.clear();
            }
            this.handleDragOver({ clientX });
            return true;
        }
        /**
         * Shared body of the dragleave entry point. Preserves anim state when
         * the drag moves between tabs-list children, into the extended drop
         * zone, or into the void container; tears it down otherwise.
         */
        _processDragLeave(related) {
            var _a, _b, _c;
            if (!this._animState) {
                return;
            }
            // Moves between children of the tabs list aren't real leaves.
            if (related && this._tabsList.contains(related)) {
                return;
            }
            // Moving into the broader drop zone (e.g. void container, left
            // actions) — keep anim state alive so external listeners can
            // continue the gap animation.
            if (related && ((_a = this._extendedDropZone) === null || _a === void 0 ? void 0 : _a.contains(related))) {
                this.resetTabTransforms();
                this._animState.currentInsertionIndex = null;
                return;
            }
            // Leaving toward the void container (empty header space to the
            // right): keep anim state so a drop can still land at the end.
            const isVoid = this._voidContainer &&
                related &&
                (related === this._voidContainer ||
                    this._voidContainer.contains(related));
            if (isVoid) {
                return;
            }
            this.resetTabTransforms();
            if (this._animState.sourceIndex === -1) {
                (_c = (_b = this.group.model.dropTargetContainer) === null || _b === void 0 ? void 0 : _b.model) === null || _c === void 0 ? void 0 : _c.clear();
                this._animState = null;
            }
            else {
                this._animState.currentInsertionIndex = null;
            }
        }
        handleDragOver(event) {
            var _a, _b, _c, _d, _e;
            if (!this._animState) {
                return;
            }
            const mouseX = event.clientX;
            let insertionIndex = null;
            let targetTabGroupId = null;
            const sourceGroupPanelIds = this._animState.sourceGroupPanelIds;
            // Accumulation approach: compute where the drag image's left edge
            // would be, then walk tabs left-to-right using their original widths.
            // A tab fits to the left of the gap if the cumulative width of all
            // preceding non-source tabs <= available space.
            const dragLeftEdge = mouseX - this._animState.cursorOffsetFromDragLeft;
            const availableSpace = dragLeftEdge - this._animState.containerLeft;
            let accWidth = 0;
            // Build lookup: first panel ID of each non-source group → group ID
            // so we can add chip widths when we encounter a group's first tab.
            const firstPanelToGroup = new Map();
            if (this._tabGroupManager.chipRenderers.size > 0) {
                const tabGroups = this.group.model.getTabGroups();
                for (const tg of tabGroups) {
                    if (tg.id === this._animState.sourceTabGroupId) {
                        continue;
                    }
                    if (tg.panelIds.length > 0) {
                        firstPanelToGroup.set(tg.panelIds[0], tg.id);
                    }
                }
            }
            for (let i = 0; i < this._tabs.length; i++) {
                const tab = this._tabs[i].value;
                if (tab.panel.id === this._animState.sourceTabId) {
                    continue;
                }
                if (sourceGroupPanelIds === null || sourceGroupPanelIds === void 0 ? void 0 : sourceGroupPanelIds.has(tab.panel.id)) {
                    continue;
                }
                // If this tab is the first of a non-source group, include
                // the chip width (which sits before it in the DOM).
                const groupId = firstPanelToGroup.get(tab.panel.id);
                if (groupId) {
                    const chipWidth = (_a = this._animState.chipPositions.get(groupId)) !== null && _a !== void 0 ? _a : 0;
                    if (accWidth + chipWidth > availableSpace) {
                        // Chip alone overflows — gap goes before this group
                        insertionIndex !== null && insertionIndex !== void 0 ? insertionIndex : (insertionIndex = i);
                        break;
                    }
                    accWidth += chipWidth;
                }
                // Use original width (before collapse/transforms)
                const origRect = this._animState.tabPositions.get(tab.panel.id);
                const tabWidth = origRect
                    ? origRect.width
                    : tab.element.getBoundingClientRect().width;
                // Shift at the midpoint: a tab moves left once the drag image
                // covers half of it (like Chrome's tab drag behavior).
                if (accWidth + tabWidth / 2 <= availableSpace) {
                    accWidth += tabWidth;
                    insertionIndex = i + 1;
                }
                else {
                    insertionIndex !== null && insertionIndex !== void 0 ? insertionIndex : (insertionIndex = i);
                    break;
                }
            }
            // Determine which tab group (if any) the insertion index falls within.
            //
            // We use snapshot-based positions (accWidth from the accumulation loop
            // above) to compute original chip boundaries.  This avoids reading
            // getBoundingClientRect() on chips whose live position is shifted by
            // the drag gap margin, which caused oscillation / visual jumps.
            if (insertionIndex !== null &&
                this._tabGroupManager.chipRenderers.size > 0) {
                const isGroupDrag = !!this._animState.sourceTabGroupId;
                const tabGroups = this.group.model.getTabGroups();
                // Rebuild the accumulated width up to insertionIndex so we know
                // the original right edge of the chip (if any) that precedes it.
                // We walk exactly the same way as the accumulation loop above.
                let accUpTo = 0;
                for (let i = 0; i < this._tabs.length; i++) {
                    const tab = this._tabs[i].value;
                    if (tab.panel.id === this._animState.sourceTabId) {
                        continue;
                    }
                    if (sourceGroupPanelIds === null || sourceGroupPanelIds === void 0 ? void 0 : sourceGroupPanelIds.has(tab.panel.id)) {
                        continue;
                    }
                    if (i >= insertionIndex) {
                        break;
                    }
                    const gid = firstPanelToGroup.get(tab.panel.id);
                    if (gid) {
                        accUpTo += (_b = this._animState.chipPositions.get(gid)) !== null && _b !== void 0 ? _b : 0;
                    }
                    const origRect = this._animState.tabPositions.get(tab.panel.id);
                    accUpTo += origRect
                        ? origRect.width
                        : tab.element.getBoundingClientRect().width;
                }
                for (const tg of tabGroups) {
                    // Build effective panel list: exclude the source tab
                    // so that dragging a tab out of its own group doesn't
                    // inflate the group's index range.
                    const effectivePanelIds = tg.panelIds.filter((pid) => pid !== this._animState.sourceTabId &&
                        !(sourceGroupPanelIds === null || sourceGroupPanelIds === void 0 ? void 0 : sourceGroupPanelIds.has(pid)));
                    if (effectivePanelIds.length === 0) {
                        continue;
                    }
                    const firstIdx = this._tabs.findIndex((t) => t.value.panel.id === effectivePanelIds[0]);
                    const lastIdx = this._tabs.findIndex((t) => t.value.panel.id ===
                        effectivePanelIds[effectivePanelIds.length - 1]);
                    if (firstIdx === -1 || lastIdx === -1) {
                        continue;
                    }
                    const isInsideRange = insertionIndex >= firstIdx && insertionIndex <= lastIdx;
                    const isJustBeforeGroup = !isInsideRange && insertionIndex === firstIdx - 1;
                    if (!isInsideRange && !isJustBeforeGroup) {
                        continue;
                    }
                    if (isGroupDrag && isInsideRange) {
                        // A group cannot be dropped inside another group.
                        // Snap the insertion index to just before or just
                        // after this group based on cursor position relative
                        // to the group's midpoint. Only applies when the
                        // insertion would land *inside* the group — for
                        // `isJustBeforeGroup`, the index is already outside
                        // (immediately left of the group) and is a valid
                        // drop position, so leave it untouched (issue #1264).
                        const groupMid = (firstIdx + lastIdx + 1) / 2;
                        if (insertionIndex < groupMid) {
                            insertionIndex = firstIdx;
                        }
                        else {
                            insertionIndex = lastIdx + 1;
                        }
                        // targetTabGroupId stays null
                        break;
                    }
                    if (isGroupDrag && isJustBeforeGroup) {
                        // Cursor is just before the group — accept this
                        // index as-is. Groups can be dropped at the slot
                        // immediately left of another group's first tab.
                        break;
                    }
                    if (isJustBeforeGroup) {
                        // Check whether only the source tab (or source group
                        // tabs) sits between insertionIndex and firstIdx.
                        // If so, the source is being dragged away from that
                        // slot, so we ARE effectively "just before" the group
                        // and should still allow dropping into position 0.
                        let allInBetweenAreSource = true;
                        for (let j = insertionIndex; j < firstIdx; j++) {
                            const pid = this._tabs[j].value.panel.id;
                            if (pid !== this._animState.sourceTabId &&
                                !(sourceGroupPanelIds === null || sourceGroupPanelIds === void 0 ? void 0 : sourceGroupPanelIds.has(pid))) {
                                allInBetweenAreSource = false;
                                break;
                            }
                        }
                        if (!allInBetweenAreSource) {
                            continue;
                        }
                        const chipWidth = (_c = this._animState.chipPositions.get(tg.id)) !== null && _c !== void 0 ? _c : 0;
                        const threshold = tg.collapsed
                            ? this._animState.containerLeft +
                                accUpTo +
                                chipWidth / 2
                            : this._animState.containerLeft + accUpTo + chipWidth;
                        if (mouseX >= threshold) {
                            insertionIndex = firstIdx;
                            targetTabGroupId = tg.id;
                        }
                        break;
                    }
                    if (isInsideRange) {
                        const chipWidth = (_d = this._animState.chipPositions.get(tg.id)) !== null && _d !== void 0 ? _d : 0;
                        const chipOriginalRight = this._animState.containerLeft + accUpTo + chipWidth;
                        if (insertionIndex === firstIdx) {
                            if (mouseX >= chipOriginalRight) {
                                targetTabGroupId = tg.id;
                            }
                        }
                        else {
                            targetTabGroupId = tg.id;
                        }
                        break;
                    }
                }
            }
            if (insertionIndex === this._animState.currentInsertionIndex &&
                targetTabGroupId === this._animState.targetTabGroupId) {
                return;
            }
            this._animState.currentInsertionIndex = insertionIndex;
            this._animState.targetTabGroupId = targetTabGroupId;
            if (((_e = this.accessor.options.theme) === null || _e === void 0 ? void 0 : _e.tabAnimation) === 'smooth') {
                this.applyDragOverTransforms();
            }
        }
        /**
         * Batch-remove a CSS class from multiple elements instantly,
         * forcing only a single reflow for the entire batch.
         */
        _removeClassInstantlyBatch(elements, cls) {
            const affected = [];
            for (const el of elements) {
                if (el.classList.contains(cls)) {
                    el.style.transition = 'none';
                    toggleClass(el, cls, false);
                    affected.push(el);
                }
            }
            if (affected.length > 0) {
                void affected[0].offsetHeight; // single reflow for entire batch
                for (const el of affected) {
                    el.style.removeProperty('transition');
                }
            }
        }
        /**
         * Remove `dv-tab--dragging` from the source tab instantly so it
         * regains its real width before FLIP snapshots.
         */
        _uncollapsSourceTab(sourceTabId) {
            const entry = this._tabMap.get(sourceTabId);
            if (entry) {
                this._removeClassInstantlyBatch([entry.value.element], 'dv-tab--dragging');
            }
        }
        applyDragOverTransforms(skipTransition = false) {
            if (!this._animState ||
                this._animState.currentInsertionIndex === null) {
                this.resetTabTransforms();
                return;
            }
            // Don't apply transforms until the source tab has been collapsed
            // in the rAF callback — otherwise the gap + visible source = jump.
            if (this._pendingCollapse) {
                return;
            }
            const insertionIndex = this._animState.currentInsertionIndex;
            // For group drags, gap = sum of all group member widths
            let gapWidth;
            const sourceGroupPanelIds = this._animState.sourceGroupPanelIds;
            if (this._animState.sourceTabGroupId && sourceGroupPanelIds) {
                gapWidth = this._animState.sourceGapWidth;
            }
            else {
                const sourceRect = this._animState.tabPositions.get(this._animState.sourceTabId);
                gapWidth = sourceRect
                    ? sourceRect.width
                    : this.getAverageTabWidth();
            }
            // When the insertion lands at or before a group's first tab, shift
            // the chip so the gap appears before the entire group.
            //
            // Two cases:
            // 1. targetTabGroupId is null (standalone drop) — always shift chip.
            // 2. targetTabGroupId is set AND the group is collapsed — shift chip
            //    because the collapsed tabs are invisible, so putting the gap on
            //    them has no visual effect.
            let chipToShift = null;
            if (this._tabGroupManager.chipRenderers.size > 0) {
                const tabGroups = this.group.model.getTabGroups();
                for (const tg of tabGroups) {
                    if (tg.id === this._animState.sourceTabGroupId)
                        continue;
                    // Skip the group that the dragged tab belongs to — the
                    // gap should appear after the chip (where the tab was),
                    // not before it.
                    if (tg.panelIds.includes(this._animState.sourceTabId))
                        continue;
                    const effectivePids = tg.panelIds.filter((pid) => pid !== this._animState.sourceTabId &&
                        !(sourceGroupPanelIds === null || sourceGroupPanelIds === void 0 ? void 0 : sourceGroupPanelIds.has(pid)));
                    if (effectivePids.length === 0)
                        continue;
                    const firstIdx = this._tabs.findIndex((t) => t.value.panel.id === effectivePids[0]);
                    // Only consider chip-shifting when dropping outside the
                    // group, or when dropping inside a collapsed group (whose
                    // tabs are invisible).
                    const shouldShiftChip = !this._animState.targetTabGroupId ||
                        (this._animState.targetTabGroupId === tg.id &&
                            tg.collapsed);
                    if (!shouldShiftChip)
                        continue;
                    if (firstIdx >= insertionIndex) {
                        let hasTabs = false;
                        for (let j = insertionIndex; j < firstIdx; j++) {
                            const pid = this._tabs[j].value.panel.id;
                            if (pid === this._animState.sourceTabId)
                                continue;
                            if (sourceGroupPanelIds === null || sourceGroupPanelIds === void 0 ? void 0 : sourceGroupPanelIds.has(pid))
                                continue;
                            hasTabs = true;
                            break;
                        }
                        if (!hasTabs) {
                            const chipEntry = this._tabGroupManager.chipRenderers.get(tg.id);
                            if (chipEntry) {
                                chipToShift = chipEntry.chip.element;
                            }
                        }
                        break;
                    }
                }
            }
            // Helper: pick the correct shifting class for tabs vs chips.
            const shiftingClass = (el) => el.classList.contains('dv-tab-group-chip')
                ? 'dv-tab-group-chip--shifting'
                : 'dv-tab--shifting';
            // Helper: apply a margin-left value to an element, optionally
            // bypassing CSS transitions for instant positioning.
            const setMargin = (el, value) => {
                if (skipTransition) {
                    el.style.transition = 'none';
                    el.style.marginLeft = value;
                    void el.offsetHeight;
                    el.style.removeProperty('transition');
                }
                else {
                    el.style.marginLeft = value;
                }
                toggleClass(el, shiftingClass(el), true);
            };
            const clearMargin = (el) => {
                const cls = shiftingClass(el);
                // Remove any previous pending listener for this element
                const prev = this._pendingMarginCleanups.get(el);
                if (prev) {
                    prev();
                }
                if (skipTransition || !el.style.marginLeft) {
                    el.style.removeProperty('margin-left');
                    toggleClass(el, cls, false);
                }
                else {
                    el.style.marginLeft = '0px';
                    toggleClass(el, cls, true);
                    const onEnd = () => {
                        el.style.removeProperty('margin-left');
                        toggleClass(el, cls, false);
                        el.removeEventListener('transitionend', onEnd);
                        clearTimeout(fallbackTimer);
                        this._pendingMarginCleanups.delete(el);
                    };
                    // Fallback in case transitionend never fires
                    // (e.g. element removed from DOM mid-transition)
                    const fallbackTimer = setTimeout(onEnd, 300);
                    this._pendingMarginCleanups.set(el, onEnd);
                    el.addEventListener('transitionend', onEnd);
                }
            };
            let gapApplied = false;
            // Reset all non-source chip margins first
            for (const [groupId, entry] of this._tabGroupManager.chipRenderers) {
                if (groupId === this._animState.sourceTabGroupId)
                    continue;
                clearMargin(entry.chip.element);
            }
            // Apply gap to chip if insertion is before a group
            if (chipToShift) {
                setMargin(chipToShift, `${gapWidth}px`);
                gapApplied = true;
            }
            for (let i = 0; i < this._tabs.length; i++) {
                const tab = this._tabs[i].value;
                if (tab.panel.id === this._animState.sourceTabId) {
                    continue;
                }
                if (sourceGroupPanelIds === null || sourceGroupPanelIds === void 0 ? void 0 : sourceGroupPanelIds.has(tab.panel.id)) {
                    continue;
                }
                if (!gapApplied && i >= insertionIndex) {
                    setMargin(tab.element, `${gapWidth}px`);
                    gapApplied = true;
                }
                else {
                    clearMargin(tab.element);
                }
            }
            // Reposition underlines to follow shifted chips/tabs
            this._tabGroupManager.trackUnderlines();
        }
        resetTabTransforms() {
            // Cancel any pending margin transitionend listeners
            for (const [, cleanup] of this._pendingMarginCleanups) {
                cleanup();
            }
            this._pendingMarginCleanups.clear();
            for (const tab of this._tabs) {
                tab.value.element.style.removeProperty('margin-left');
                tab.value.element.style.removeProperty('margin-right');
                tab.value.element.style.removeProperty('margin-top');
                tab.value.element.style.removeProperty('margin-bottom');
                tab.value.element.style.removeProperty('transform');
                toggleClass(tab.value.element, 'dv-tab--shifting', false);
            }
            for (const [, entry] of this._tabGroupManager.chipRenderers) {
                entry.chip.element.style.removeProperty('margin-left');
                toggleClass(entry.chip.element, 'dv-tab-group-chip--shifting', false);
            }
            this._tabGroupManager.positionUnderlines();
        }
        /**
         * Commit a group-drag drop: clear drag classes, move the group
         * in the model, and run a FLIP animation.
         */
        _commitGroupMove(sourceTabGroupId, insertionIndex) {
            var _a, _b;
            // Read transfer data first.
            const data = getPanelData();
            // Synchronously dispose the source chip's drag sources, which
            // clears the panelTransfer payload + iframe shield. Cross-group
            // moves dissolve the source chip on a microtask, which is too
            // late: a synchronous `getPanelData()` after this method (or any
            // sibling dragover handler firing in the same tick) would
            // otherwise see stale data still referencing the old tabGroupId.
            this._tabGroupManager.disposeChipDrag(sourceTabGroupId);
            // Check if the tab group exists in this group (within-group reorder)
            // or in another group (cross-group move).
            const isLocal = this.group.model
                .getTabGroups()
                .some((tg) => tg.id === sourceTabGroupId);
            if (isLocal) {
                if (((_a = this.accessor.options.theme) === null || _a === void 0 ? void 0 : _a.tabAnimation) === 'smooth') {
                    this._clearGroupDragClasses(sourceTabGroupId);
                    const firstPositions = this.snapshotTabPositions();
                    this.resetTabTransforms();
                    this.group.model.moveTabGroup(sourceTabGroupId, insertionIndex);
                    this.runFlipAnimation(firstPositions, '', false);
                }
                else {
                    this._tabGroupManager.skipNextCollapseAnimation = true;
                    this.group.model.moveTabGroup(sourceTabGroupId, insertionIndex);
                }
            }
            else if (data) {
                // Cross-group: delegate to the component-level move which
                // handles panel transfer and tab group recreation.
                // Use the REAL tab group ID from transfer data, not the
                // potentially stale one from _animState.
                //
                // Clear any inline gap margin / shifting class applied to
                // destination tabs during dragover. Cross-group moves don't
                // run the FLIP path, and `moveGroupOrPanel` only inserts new
                // panels — it doesn't recreate existing destination tabs, so
                // their inline `margin-left` would otherwise persist as a
                // visible gap (issue #1243).
                this.resetTabTransforms();
                this.accessor.moveGroupOrPanel({
                    from: {
                        groupId: data.groupId,
                        tabGroupId: (_b = data.tabGroupId) !== null && _b !== void 0 ? _b : sourceTabGroupId,
                    },
                    to: {
                        group: this.group,
                        position: 'center',
                        index: insertionIndex,
                    },
                });
            }
        }
        _clearGroupDragClasses(sourceTabGroupId) {
            const chipEntry = this._tabGroupManager.chipRenderers.get(sourceTabGroupId);
            if (chipEntry) {
                this._removeClassInstantlyBatch([chipEntry.chip.element], 'dv-tab-group-chip--dragging');
            }
            this._removeClassInstantlyBatch(this._tabs.map((t) => t.value.element), 'dv-tab--dragging');
            // Restore underline
            const underline = this._tabGroupManager.groupUnderlines.get(sourceTabGroupId);
            if (underline) {
                underline.style.removeProperty('display');
            }
            // The subsequent moveTabGroup will re-create tabs and call
            // updateTabGroups → _updateTabGroupClasses. For collapsed groups
            // the new tabs don't have dv-tab--group-collapsed yet, which
            // would trigger the collapse animation. Skip it.
            this._tabGroupManager.skipNextCollapseAnimation = true;
        }
        resetDragAnimation() {
            this._pendingCollapse = false;
            // After a drop, `tab.onDrop` consumes _animState (sets it to null)
            // and immediately calls `runFlipAnimation`, which sets transforms
            // and queues an rAF to trigger the CSS transition. dragend fires
            // synchronously on the source element BEFORE that rAF runs — if
            // we cleared transforms here we'd clobber the in-flight FLIP, so
            // gate the cleanup on _animState still being set (i.e. drag was
            // cancelled rather than dropped).
            if (this._animState) {
                this.resetTabTransforms();
                if (this._animState.sourceTabGroupId) {
                    this._clearGroupDragClasses(this._animState.sourceTabGroupId);
                }
                else {
                    this._removeClassInstantlyBatch(this._tabs.map((t) => t.value.element), 'dv-tab--dragging');
                }
                this._animState = null;
                // Restore any hidden underlines from group drags.
                for (const [, el] of this._tabGroupManager.groupUnderlines) {
                    el.style.removeProperty('display');
                }
            }
        }
        runFlipAnimation(firstPositions, sourceTabId, isCrossGroup = false, animRange) {
            const isVertical = this._direction === 'vertical';
            let hasAnimation = false;
            for (let i = 0; i < this._tabs.length; i++) {
                const tab = this._tabs[i];
                const panelId = tab.value.panel.id;
                if (panelId === sourceTabId) {
                    if (isCrossGroup) {
                        // Newly inserted tab: slide in from the end
                        const rect = tab.value.element.getBoundingClientRect();
                        tab.value.element.style.transform = isVertical
                            ? `translateY(${rect.height}px)`
                            : `translateX(${rect.width}px)`;
                        toggleClass(tab.value.element, 'dv-tab--shifting', true);
                        hasAnimation = true;
                    }
                    continue;
                }
                // Skip tabs outside the affected range (they don't logically move)
                if (animRange !== undefined &&
                    (i < animRange.from || i > animRange.to)) {
                    continue;
                }
                const firstRect = firstPositions.get(panelId);
                if (!firstRect) {
                    continue;
                }
                const lastRect = tab.value.element.getBoundingClientRect();
                const delta = isVertical
                    ? firstRect.top - lastRect.top
                    : firstRect.left - lastRect.left;
                if (Math.abs(delta) < 1) {
                    continue;
                }
                tab.value.element.style.transform = isVertical
                    ? `translateY(${delta}px)`
                    : `translateX(${delta}px)`;
                toggleClass(tab.value.element, 'dv-tab--shifting', true);
                hasAnimation = true;
            }
            if (!hasAnimation) {
                return;
            }
            requestAnimationFrame(() => {
                var _a;
                for (const tab of this._tabs) {
                    if (tab.value.element.style.transform) {
                        tab.value.element.style.transform = '';
                    }
                }
                // Track underlines during the FLIP transition so they
                // follow tabs as they slide to their final positions.
                this._tabGroupManager.trackUnderlines();
                // Clean up any previous flip transition listener
                (_a = this._flipTransitionCleanup) === null || _a === void 0 ? void 0 : _a.call(this);
                const onTransitionEnd = (event) => {
                    if (event.propertyName === 'transform') {
                        cleanup();
                        for (const tab of this._tabs) {
                            toggleClass(tab.value.element, 'dv-tab--shifting', false);
                        }
                        // Final reposition after animation settles
                        this._tabGroupManager.positionUnderlines();
                    }
                };
                const cleanup = () => {
                    this._tabsList.removeEventListener('transitionend', onTransitionEnd);
                    this._flipTransitionCleanup = null;
                };
                this._flipTransitionCleanup = cleanup;
                this._tabsList.addEventListener('transitionend', onTransitionEnd);
            });
        }
    }

    const createSvgElementFromPath = (params) => {
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttributeNS(null, 'height', params.height);
        svg.setAttributeNS(null, 'width', params.width);
        svg.setAttributeNS(null, 'viewBox', params.viewbox);
        svg.setAttributeNS(null, 'aria-hidden', 'false');
        svg.setAttributeNS(null, 'focusable', 'false');
        svg.classList.add('dv-svg');
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttributeNS(null, 'd', params.path);
        svg.appendChild(path);
        return svg;
    };
    const createCloseButton = () => createSvgElementFromPath({
        width: '11',
        height: '11',
        viewbox: '0 0 28 28',
        path: 'M2.1 27.3L0 25.2L11.55 13.65L0 2.1L2.1 0L13.65 11.55L25.2 0L27.3 2.1L15.75 13.65L27.3 25.2L25.2 27.3L13.65 15.75L2.1 27.3Z',
    });
    const createExpandMoreButton = () => createSvgElementFromPath({
        width: '11',
        height: '11',
        viewbox: '0 0 24 15',
        path: 'M12 14.15L0 2.15L2.15 0L12 9.9L21.85 0.0499992L24 2.2L12 14.15Z',
    });
    const createChevronRightButton = () => createSvgElementFromPath({
        width: '11',
        height: '11',
        viewbox: '0 0 15 25',
        path: 'M2.15 24.1L0 21.95L9.9 12.05L0 2.15L2.15 0L14.2 12.05L2.15 24.1Z',
    });

    function createDropdownElementHandle() {
        const el = document.createElement('div');
        el.className = 'dv-tabs-overflow-dropdown-default';
        const text = document.createElement('span');
        text.textContent = ``;
        const icon = createChevronRightButton();
        el.appendChild(icon);
        el.appendChild(text);
        return {
            element: el,
            update: (params) => {
                text.textContent = `${params.tabs}`;
            },
        };
    }

    class TabsContainer extends CompositeDisposable {
        get onTabDragStart() {
            return this.tabs.onTabDragStart;
        }
        get panels() {
            return this.tabs.panels;
        }
        get size() {
            return this.tabs.size;
        }
        get hidden() {
            return this._hidden;
        }
        set hidden(value) {
            this._hidden = value;
            this.element.style.display = value ? 'none' : '';
        }
        get direction() {
            return this._direction;
        }
        set direction(value) {
            this._direction = value;
            if (value === 'vertical') {
                addClasses(this._element, 'dv-groupview-header-vertical');
                addClasses(this.rightActionsContainer, 'dv-right-actions-container-vertical');
                this.tabs.direction = value;
            }
            else {
                removeClasses(this._element, 'dv-groupview-header-vertical');
                removeClasses(this.rightActionsContainer, 'dv-right-actions-container-vertical');
                this.tabs.direction = value;
            }
        }
        get element() {
            return this._element;
        }
        constructor(accessor, group) {
            super();
            this.accessor = accessor;
            this.group = group;
            this._hidden = false;
            this._direction = 'horizontal';
            this.dropdownPart = null;
            this._overflowTabs = [];
            this._overflowTabGroups = [];
            this._dropdownDisposable = new MutableDisposable();
            this._onDrop = new Emitter();
            this.onDrop = this._onDrop.event;
            this._onGroupDragStart = new Emitter();
            this.onGroupDragStart = this._onGroupDragStart.event;
            this._onWillShowOverlay = new Emitter();
            this.onWillShowOverlay = this._onWillShowOverlay.event;
            this._element = document.createElement('div');
            this._element.className = 'dv-tabs-and-actions-container';
            toggleClass(this._element, 'dv-full-width-single-tab', this.accessor.options.singleTabMode === 'fullwidth');
            this.rightActionsContainer = document.createElement('div');
            this.rightActionsContainer.className = 'dv-right-actions-container';
            this.leftActionsContainer = document.createElement('div');
            this.leftActionsContainer.className = 'dv-left-actions-container';
            this.preActionsContainer = document.createElement('div');
            this.preActionsContainer.className = 'dv-pre-actions-container';
            this.tabs = new Tabs(group, accessor, {
                showTabsOverflowControl: !accessor.options.disableTabsOverflowList,
            });
            this.voidContainer = new VoidContainer(this.accessor, this.group);
            this.tabs.voidContainer = this.voidContainer.element;
            this._element.appendChild(this.preActionsContainer);
            this._element.appendChild(this.tabs.element);
            this._element.appendChild(this.leftActionsContainer);
            this._element.appendChild(this.voidContainer.element);
            this._element.appendChild(this.rightActionsContainer);
            this.tabs.setExtendedDropZone(this._element);
            this.addDisposables(this.tabs.onDrop((e) => this._onDrop.fire(e)), this.tabs.onWillShowOverlay((e) => this._onWillShowOverlay.fire(e)), accessor.onDidOptionsChange(() => {
                this.tabs.showTabsOverflowControl =
                    !accessor.options.disableTabsOverflowList;
            }), this.tabs.onOverflowTabsChange((event) => {
                this.toggleDropdown(event);
            }), this.tabs, this._onWillShowOverlay, this._onDrop, this._onGroupDragStart, this.voidContainer, this.voidContainer.onDragStart((event) => {
                this._onGroupDragStart.fire({
                    nativeEvent: event,
                    group: this.group,
                });
            }), this.voidContainer.onDrop((event) => {
                // If an active group drag is in progress, let Tabs handle it
                if (this.tabs.handleVoidDrop()) {
                    return;
                }
                this._onDrop.fire({
                    event: event.nativeEvent,
                    index: this.tabs.size,
                });
            }), this.voidContainer.onWillShowOverlay((event) => {
                this._onWillShowOverlay.fire(new DockviewWillShowOverlayLocationEvent(event, {
                    kind: 'header_space',
                    panel: this.group.activePanel,
                    api: this.accessor.api,
                    group: this.group,
                    getData: getPanelData,
                }));
            }), addDisposableListener(this.leftActionsContainer, 'dragleave', (event) => {
                const related = event.relatedTarget;
                if (!this.leftActionsContainer.contains(related) &&
                    !this._element.contains(related)) {
                    // Left the header entirely
                    this.tabs.clearExternalAnimState();
                }
            }), addDisposableListener(this.voidContainer.element, 'dragleave', (event) => {
                const related = event.relatedTarget;
                if (!this.voidContainer.element.contains(related)) {
                    if (this._element.contains(related)) {
                        // Moved to another part of the header — keep state
                        this.tabs.setExternalInsertionIndex(null);
                    }
                    else {
                        // Left the header entirely
                        this.tabs.clearExternalAnimState();
                    }
                }
            }), addDisposableListener(this.voidContainer.element, 'pointerdown', (event) => {
                if (event.defaultPrevented) {
                    return;
                }
                const isFloatingGroupsEnabled = !this.accessor.options.disableFloatingGroups;
                if (isFloatingGroupsEnabled &&
                    event.shiftKey &&
                    this.group.api.location.type !== 'floating' &&
                    this.group.api.location.type !== 'edge') {
                    event.preventDefault();
                    const { top, left } = this.element.getBoundingClientRect();
                    const { top: rootTop, left: rootLeft } = this.accessor.element.getBoundingClientRect();
                    this.accessor.addFloatingGroup(this.group, {
                        x: left - rootLeft + 20,
                        y: top - rootTop + 20,
                        inDragMode: true,
                    });
                }
            }));
        }
        show() {
            if (!this.hidden) {
                this.element.style.display = '';
            }
        }
        hide() {
            this._element.style.display = 'none';
        }
        setRightActionsElement(element) {
            if (this.rightActions === element) {
                return;
            }
            if (this.rightActions) {
                this.rightActions.remove();
                this.rightActions = undefined;
            }
            if (element) {
                this.rightActionsContainer.appendChild(element);
                this.rightActions = element;
            }
        }
        setLeftActionsElement(element) {
            if (this.leftActions === element) {
                return;
            }
            if (this.leftActions) {
                this.leftActions.remove();
                this.leftActions = undefined;
            }
            if (element) {
                this.leftActionsContainer.appendChild(element);
                this.leftActions = element;
            }
        }
        setPrefixActionsElement(element) {
            if (this.preActions === element) {
                return;
            }
            if (this.preActions) {
                this.preActions.remove();
                this.preActions = undefined;
            }
            if (element) {
                this.preActionsContainer.appendChild(element);
                this.preActions = element;
            }
        }
        isActive(tab) {
            return this.tabs.isActive(tab);
        }
        indexOf(id) {
            return this.tabs.indexOf(id);
        }
        setActive(_isGroupActive) {
            // noop
        }
        delete(id) {
            this.tabs.delete(id);
            this.updateClassnames();
        }
        setActivePanel(panel) {
            this.tabs.setActivePanel(panel);
        }
        openPanel(panel, index = this.tabs.size) {
            this.tabs.openPanel(panel, index);
            this.updateClassnames();
        }
        closePanel(panel) {
            this.delete(panel.id);
        }
        updateClassnames() {
            toggleClass(this._element, 'dv-single-tab', this.size === 1);
        }
        toggleDropdown(options) {
            const tabs = options.reset ? [] : options.tabs;
            const tabGroups = options.reset ? [] : options.tabGroups;
            this._overflowTabs = tabs;
            this._overflowTabGroups = tabGroups;
            const totalCount = this._overflowTabs.length;
            if (totalCount > 0 && this.dropdownPart) {
                this.dropdownPart.update({ tabs: totalCount });
                return;
            }
            if (totalCount === 0) {
                this._dropdownDisposable.dispose();
                return;
            }
            const root = document.createElement('div');
            root.className = 'dv-tabs-overflow-dropdown-root';
            const part = createDropdownElementHandle();
            part.update({ tabs: totalCount });
            this.dropdownPart = part;
            root.appendChild(part.element);
            this.rightActionsContainer.prepend(root);
            this._dropdownDisposable.value = new CompositeDisposable(exports.DockviewDisposable.from(() => {
                var _a, _b;
                root.remove();
                (_b = (_a = this.dropdownPart) === null || _a === void 0 ? void 0 : _a.dispose) === null || _b === void 0 ? void 0 : _b.call(_a);
                this.dropdownPart = null;
            }), addDisposableListener(root, 'pointerdown', (event) => {
                event.preventDefault();
            }, { capture: true }), addDisposableListener(root, 'click', (event) => {
                const el = document.createElement('div');
                el.style.overflow = 'auto';
                el.className = 'dv-tabs-overflow-container';
                // Build lookup: panelId → tabGroup for overflow groups
                const overflowGroupSet = new Set(this._overflowTabGroups);
                const allTabGroups = this.group.model.getTabGroups();
                const panelToGroup = new Map();
                for (const tg of allTabGroups) {
                    if (overflowGroupSet.has(tg.id)) {
                        for (const pid of tg.panelIds) {
                            panelToGroup.set(pid, tg);
                        }
                    }
                }
                // Track which groups have already been rendered
                const renderedGroups = new Set();
                for (const tab of this.tabs.tabs.filter((tab) => this._overflowTabs.includes(tab.panel.id))) {
                    const tg = panelToGroup.get(tab.panel.id);
                    // If this tab belongs to an overflow group, render the
                    // group header before its first member tab.
                    if (tg && !renderedGroups.has(tg.id)) {
                        renderedGroups.add(tg.id);
                        const groupHeader = document.createElement('div');
                        groupHeader.className = 'dv-tabs-overflow-group-header';
                        const colorDot = document.createElement('span');
                        colorDot.className = 'dv-tabs-overflow-group-color';
                        applyTabGroupAccent(colorDot, tg.color, this.accessor.tabGroupColorPalette);
                        groupHeader.appendChild(colorDot);
                        const labelSpan = document.createElement('span');
                        labelSpan.className = 'dv-tabs-overflow-group-label';
                        labelSpan.textContent = tg.label || tg.id;
                        groupHeader.appendChild(labelSpan);
                        if (tg.collapsed) {
                            const badge = document.createElement('span');
                            badge.className =
                                'dv-tabs-overflow-group-collapsed-badge';
                            badge.textContent = `${tg.panelIds.length}`;
                            groupHeader.appendChild(badge);
                        }
                        groupHeader.addEventListener('click', () => {
                            this.accessor
                                .getPopupServiceForGroup(this.group)
                                .close();
                            if (tg.collapsed) {
                                tg.expand();
                            }
                            // Activate the first panel in the group
                            const firstPanelId = tg.panelIds[0];
                            if (firstPanelId) {
                                const panel = this.group.panels.find((p) => p.id === firstPanelId);
                                panel === null || panel === void 0 ? void 0 : panel.api.setActive();
                            }
                        });
                        el.appendChild(groupHeader);
                    }
                    const panelObject = this.group.panels.find((panel) => panel === tab.panel);
                    const tabComponent = panelObject.view.createTabRenderer('headerOverflow');
                    const child = tabComponent.element;
                    const wrapper = document.createElement('div');
                    toggleClass(wrapper, 'dv-tab', true);
                    toggleClass(wrapper, 'dv-active-tab', panelObject.api.isActive);
                    toggleClass(wrapper, 'dv-inactive-tab', !panelObject.api.isActive);
                    if (tg) {
                        toggleClass(wrapper, 'dv-tab--grouped', true);
                    }
                    wrapper.addEventListener('click', (event) => {
                        this.accessor
                            .getPopupServiceForGroup(this.group)
                            .close();
                        if (event.defaultPrevented) {
                            return;
                        }
                        if (tg === null || tg === void 0 ? void 0 : tg.collapsed) {
                            tg.expand();
                        }
                        tab.element.scrollIntoView();
                        tab.panel.api.setActive();
                    });
                    wrapper.appendChild(child);
                    el.appendChild(wrapper);
                }
                const relativeParent = findRelativeZIndexParent(root);
                this.accessor
                    .getPopupServiceForGroup(this.group)
                    .openPopover(el, {
                    x: event.clientX,
                    y: event.clientY,
                    zIndex: (relativeParent === null || relativeParent === void 0 ? void 0 : relativeParent.style.zIndex)
                        ? `calc(${relativeParent.style.zIndex} * 2)`
                        : undefined,
                });
            }));
        }
        updateDragAndDropState() {
            this.tabs.updateDragAndDropState();
            this.voidContainer.updateDragAndDropState();
        }
        updateTabGroups() {
            this.tabs.updateTabGroups();
        }
        refreshTabGroupAccent() {
            this.tabs.refreshTabGroupAccent();
        }
    }

    class DockviewUnhandledDragOverEvent extends AcceptableEvent {
        constructor(nativeEvent, target, position, getData, group) {
            super();
            this.nativeEvent = nativeEvent;
            this.target = target;
            this.position = position;
            this.getData = getData;
            this.group = group;
        }
    }
    const PROPERTY_KEYS_DOCKVIEW = (() => {
        /**
         * by readong the keys from an empty value object TypeScript will error
         * when we add or remove new properties to `DockviewOptions`
         */
        const properties = {
            disableAutoResizing: undefined,
            hideBorders: undefined,
            singleTabMode: undefined,
            disableFloatingGroups: undefined,
            floatingGroupBounds: undefined,
            popoutUrl: undefined,
            nonce: undefined,
            defaultRenderer: undefined,
            defaultHeaderPosition: undefined,
            debug: undefined,
            rootOverlayModel: undefined,
            locked: undefined,
            disableDnd: undefined,
            dndStrategy: undefined,
            className: undefined,
            noPanelsOverlay: undefined,
            dndEdges: undefined,
            theme: undefined,
            disableTabsOverflowList: undefined,
            scrollbars: undefined,
            getTabContextMenuItems: undefined,
            getTabGroupChipContextMenuItems: undefined,
            createTabGroupChipComponent: undefined,
            createGroupDragGhostComponent: undefined,
            tabGroupColors: undefined,
            tabGroupAccent: undefined,
        };
        return Object.keys(properties);
    })();
    function isPanelOptionsWithPanel(data) {
        if (data.referencePanel) {
            return true;
        }
        return false;
    }
    function isPanelOptionsWithGroup(data) {
        if (data.referenceGroup) {
            return true;
        }
        return false;
    }
    function isGroupOptionsWithPanel(data) {
        if (data.referencePanel) {
            return true;
        }
        return false;
    }
    function isGroupOptionsWithGroup(data) {
        if (data.referenceGroup) {
            return true;
        }
        return false;
    }

    class TabGroup extends CompositeDisposable {
        get label() {
            return this._label;
        }
        get color() {
            return this._color;
        }
        get componentParams() {
            return this._componentParams;
        }
        setLabel(value) {
            if (this.isDisposed || this._label === value) {
                return;
            }
            this._label = value;
            this._onDidChange.fire();
        }
        setColor(value) {
            if (this.isDisposed) {
                return;
            }
            const next = value === '' ? undefined : value;
            if (this._color === next) {
                return;
            }
            this._color = next;
            this._onDidChange.fire();
        }
        setComponentParams(value) {
            if (this.isDisposed) {
                return;
            }
            this._componentParams = value;
            this._onDidChange.fire();
        }
        get collapsed() {
            return this._collapsed;
        }
        get panelIds() {
            return this._panelIds;
        }
        get size() {
            return this._panelIds.length;
        }
        get isEmpty() {
            return this._panelIds.length === 0;
        }
        constructor(id, options) {
            var _a, _b;
            super();
            this.id = id;
            this._collapsed = false;
            this._panelIds = [];
            this._onDidChange = new Emitter();
            this.onDidChange = this._onDidChange.event;
            this._onDidPanelChange = new Emitter();
            this.onDidPanelChange = this._onDidPanelChange.event;
            this._onDidCollapseChange = new Emitter();
            this.onDidCollapseChange = this._onDidCollapseChange.event;
            this._onDidDestroy = new Emitter();
            this.onDidDestroy = this._onDidDestroy.event;
            this._label = (_a = options === null || options === void 0 ? void 0 : options.label) !== null && _a !== void 0 ? _a : '';
            this._color = (options === null || options === void 0 ? void 0 : options.color) === '' ? undefined : options === null || options === void 0 ? void 0 : options.color;
            this._collapsed = (_b = options === null || options === void 0 ? void 0 : options.collapsed) !== null && _b !== void 0 ? _b : false;
            this._componentParams = options === null || options === void 0 ? void 0 : options.componentParams;
            this.addDisposables(this._onDidChange, this._onDidPanelChange, this._onDidCollapseChange, this._onDidDestroy);
        }
        addPanel(panelId, index) {
            if (this.isDisposed) {
                return;
            }
            if (this._panelIds.includes(panelId)) {
                return;
            }
            const insertIndex = index !== undefined
                ? Math.max(0, Math.min(index, this._panelIds.length))
                : this._panelIds.length;
            this._panelIds.splice(insertIndex, 0, panelId);
            this._onDidPanelChange.fire({ panelId, type: 'add' });
        }
        removePanel(panelId) {
            if (this.isDisposed) {
                return false;
            }
            const index = this._panelIds.indexOf(panelId);
            if (index === -1) {
                return false;
            }
            this._panelIds.splice(index, 1);
            this._onDidPanelChange.fire({ panelId, type: 'remove' });
            return true;
        }
        indexOfPanel(panelId) {
            return this._panelIds.indexOf(panelId);
        }
        containsPanel(panelId) {
            return this._panelIds.includes(panelId);
        }
        collapse() {
            if (this.isDisposed || this._collapsed) {
                return;
            }
            this._collapsed = true;
            this._onDidCollapseChange.fire(true);
        }
        expand() {
            if (this.isDisposed || !this._collapsed) {
                return;
            }
            this._collapsed = false;
            this._onDidCollapseChange.fire(false);
        }
        toggle() {
            if (this._collapsed) {
                this.expand();
            }
            else {
                this.collapse();
            }
        }
        toJSON() {
            const result = {
                id: this.id,
                collapsed: this._collapsed,
                panelIds: [...this._panelIds],
            };
            if (this._label) {
                result.label = this._label;
            }
            if (this._color !== undefined) {
                result.color = this._color;
            }
            if (this._componentParams !== undefined) {
                result.componentParams = this._componentParams;
            }
            return result;
        }
        dispose() {
            this._onDidDestroy.fire();
            super.dispose();
        }
    }

    class DockviewDidDropEvent extends DockviewEvent {
        /**
         * `PointerEvent` for touch drags has no `dataTransfer`; use
         * `getData()` for the dockview payload regardless of input method.
         */
        get nativeEvent() {
            return this.options.nativeEvent;
        }
        get position() {
            return this.options.position;
        }
        get panel() {
            return this.options.panel;
        }
        get group() {
            return this.options.group;
        }
        get api() {
            return this.options.api;
        }
        constructor(options) {
            super();
            this.options = options;
        }
        getData() {
            return this.options.getData();
        }
    }
    class DockviewWillDropEvent extends DockviewDidDropEvent {
        get kind() {
            return this._kind;
        }
        constructor(options) {
            super(options);
            this._kind = options.kind;
        }
    }
    class DockviewGroupPanelModel extends CompositeDisposable {
        get tabGroups() {
            return this._tabGroups;
        }
        get element() {
            throw new Error('dockview: not supported');
        }
        get activePanel() {
            return this._activePanel;
        }
        get locked() {
            return this._locked;
        }
        set locked(value) {
            this._locked = value;
            toggleClass(this.container, 'dv-locked-groupview', value === 'no-drop-target' || value);
        }
        get isActive() {
            return this._isGroupActive;
        }
        get panels() {
            return this._panels;
        }
        get size() {
            return this._panels.length;
        }
        get isEmpty() {
            return this._panels.length === 0;
        }
        get hasWatermark() {
            return !!(this.watermark && this.container.contains(this.watermark.element));
        }
        get header() {
            return this.tabsContainer;
        }
        get isContentFocused() {
            if (!document.activeElement) {
                return false;
            }
            return isAncestor(document.activeElement, this.contentContainer.element);
        }
        get headerPosition() {
            var _a;
            return (_a = this._headerPosition) !== null && _a !== void 0 ? _a : 'top';
        }
        set headerPosition(value) {
            var _a;
            this._headerPosition = value;
            removeClasses(this.container, 'dv-groupview-header-top', 'dv-groupview-header-bottom', 'dv-groupview-header-left', 'dv-groupview-header-right');
            addClasses(this.container, `dv-groupview-header-${value}`);
            const direction = value === 'top' || value === 'bottom' ? 'horizontal' : 'vertical';
            this.tabsContainer.direction = direction;
            this.header.direction = direction;
            // resize the active panel to fit the new header direction
            // if not, the panel will overflow the tabs container
            if ((_a = this._activePanel) === null || _a === void 0 ? void 0 : _a.layout) {
                this._activePanel.layout(this._width, this._height);
            }
            if (this._leftHeaderActions ||
                this._rightHeaderActions ||
                this._prefixHeaderActions) {
                this.updateHeaderActions();
            }
        }
        get location() {
            return this._location;
        }
        set location(value) {
            this._location = value;
            toggleClass(this.container, 'dv-groupview-floating', false);
            toggleClass(this.container, 'dv-groupview-popout', false);
            toggleClass(this.container, 'dv-groupview-edge', false);
            // Mouse and touch drop targets must agree on accepted zones.
            const applyZones = (zones) => {
                this.contentContainer.dropTarget.setTargetZones(zones);
                this.contentContainer.pointerDropTarget.setTargetZones(zones);
            };
            switch (value.type) {
                case 'grid':
                    applyZones(['top', 'bottom', 'left', 'right', 'center']);
                    break;
                case 'floating':
                    applyZones(['center']);
                    applyZones(value
                        ? ['center']
                        : ['top', 'bottom', 'left', 'right', 'center']);
                    toggleClass(this.container, 'dv-groupview-floating', true);
                    break;
                case 'popout':
                    applyZones(['center']);
                    toggleClass(this.container, 'dv-groupview-popout', true);
                    break;
                case 'edge':
                    applyZones(['center']);
                    toggleClass(this.container, 'dv-groupview-edge', true);
                    break;
            }
            this.groupPanel.api._onDidLocationChange.fire({
                location: this.location,
            });
        }
        constructor(container, accessor, id, options, groupPanel) {
            var _a, _b;
            super();
            this.container = container;
            this.accessor = accessor;
            this.id = id;
            this.options = options;
            this.groupPanel = groupPanel;
            this._isGroupActive = false;
            this._locked = false;
            this._rightHeaderActionsDisposable = new MutableDisposable();
            this._leftHeaderActionsDisposable = new MutableDisposable();
            this._prefixHeaderActionsDisposable = new MutableDisposable();
            this._location = { type: 'grid' };
            this.mostRecentlyUsed = [];
            this._overwriteRenderContainer = null;
            this._overwriteDropTargetContainer = null;
            this._onDidChange = new Emitter();
            this.onDidChange = this._onDidChange.event;
            this._width = 0;
            this._height = 0;
            this._panels = [];
            this._panelDisposables = new Map();
            this._tabGroupDisposables = new Map();
            this._pendingMicrotaskDisposables = new Set();
            this._onMove = new Emitter();
            this.onMove = this._onMove.event;
            this._onDidDrop = new Emitter();
            this.onDidDrop = this._onDidDrop.event;
            this._onWillDrop = new Emitter();
            this.onWillDrop = this._onWillDrop.event;
            this._onWillShowOverlay = new Emitter();
            this.onWillShowOverlay = this._onWillShowOverlay.event;
            this._onTabDragStart = new Emitter();
            this.onTabDragStart = this._onTabDragStart.event;
            this._onGroupDragStart = new Emitter();
            this.onGroupDragStart = this._onGroupDragStart.event;
            this._onDidAddPanel = new Emitter();
            this.onDidAddPanel = this._onDidAddPanel.event;
            this._onDidPanelTitleChange = new Emitter();
            this.onDidPanelTitleChange = this._onDidPanelTitleChange.event;
            this._onDidPanelParametersChange = new Emitter();
            this.onDidPanelParametersChange = this._onDidPanelParametersChange.event;
            this._onDidRemovePanel = new Emitter();
            this.onDidRemovePanel = this._onDidRemovePanel.event;
            this._onDidActivePanelChange = new Emitter();
            this.onDidActivePanelChange = this._onDidActivePanelChange.event;
            this._onUnhandledDragOverEvent = new Emitter();
            this.onUnhandledDragOverEvent = this._onUnhandledDragOverEvent.event;
            this._tabGroups = [];
            this._tabGroupMap = new Map();
            this._panelToTabGroup = new Map();
            this._tabGroupIdCounter = 0;
            this._pendingTabGroupUpdate = false;
            this._onDidCreateTabGroup = new Emitter();
            this.onDidCreateTabGroup = this._onDidCreateTabGroup.event;
            this._onDidDestroyTabGroup = new Emitter();
            this.onDidDestroyTabGroup = this._onDidDestroyTabGroup.event;
            this._onDidAddPanelToTabGroup = new Emitter();
            this.onDidAddPanelToTabGroup = this._onDidAddPanelToTabGroup.event;
            this._onDidRemovePanelFromTabGroup = new Emitter();
            this.onDidRemovePanelFromTabGroup = this._onDidRemovePanelFromTabGroup.event;
            this._onDidTabGroupChange = new Emitter();
            this.onDidTabGroupChange = this._onDidTabGroupChange.event;
            this._onDidTabGroupCollapsedChange = new Emitter();
            this.onDidTabGroupCollapsedChange = this._onDidTabGroupCollapsedChange.event;
            toggleClass(this.container, 'dv-groupview', true);
            this._api = new DockviewApi(this.accessor);
            this.tabsContainer = new TabsContainer(this.accessor, this.groupPanel);
            this.contentContainer = new ContentContainer(this.accessor, this);
            container.append(this.tabsContainer.element, this.contentContainer.element);
            this.header.hidden = !!options.hideHeader;
            this.locked = (_a = options.locked) !== null && _a !== void 0 ? _a : false;
            this.headerPosition =
                (_b = options.headerPosition) !== null && _b !== void 0 ? _b : accessor.defaultHeaderPosition;
            this.addDisposables(this._onTabDragStart, this._onGroupDragStart, this._onWillShowOverlay, this._rightHeaderActionsDisposable, this._leftHeaderActionsDisposable, this._prefixHeaderActionsDisposable, this.tabsContainer.onTabDragStart((event) => {
                this._onTabDragStart.fire(event);
            }), this.tabsContainer.onGroupDragStart((event) => {
                this._onGroupDragStart.fire(event);
            }), this.tabsContainer.onDrop((event) => {
                var _a;
                // Capture panel data before handleDropEvent (which may trigger moves)
                const dragData = getPanelData();
                const draggedPanelId = (_a = dragData === null || dragData === void 0 ? void 0 : dragData.panelId) !== null && _a !== void 0 ? _a : null;
                this.handleDropEvent('header', event.event, 'center', event.index);
                // Update tab group membership after the move completes
                if (draggedPanelId && event.targetTabGroupId) {
                    // Compute the local index within the target tab group
                    // from the global panel index so the panel is inserted
                    // at the correct position, not just appended.
                    const tabGroup = this._tabGroupMap.get(event.targetTabGroupId);
                    let localIndex;
                    if (tabGroup) {
                        const globalIdx = this._panels.findIndex((p) => p.id === draggedPanelId);
                        if (globalIdx !== -1) {
                            // Count how many of this group's panels
                            // appear before the dragged panel
                            localIndex = 0;
                            for (const pid of tabGroup.panelIds) {
                                const pidIdx = this._panels.findIndex((p) => p.id === pid);
                                if (pidIdx < globalIdx) {
                                    localIndex++;
                                }
                            }
                        }
                    }
                    this.addPanelToTabGroup(event.targetTabGroupId, draggedPanelId, localIndex);
                }
                else if (draggedPanelId && event.targetTabGroupId === null) {
                    // Dropped outside any group — remove from current group
                    this.removePanelFromTabGroup(draggedPanelId);
                }
            }), this.contentContainer.onDidFocus(() => {
                this.accessor.doSetGroupActive(this.groupPanel);
            }), this.contentContainer.onDidBlur(() => {
                // noop
            }), this.contentContainer.dropTarget.onDrop((event) => {
                this.handleDropEvent('content', event.nativeEvent, event.position);
            }), this.contentContainer.pointerDropTarget.onDrop((event) => {
                this.handleDropEvent('content', event.nativeEvent, event.position);
            }), this.tabsContainer.onWillShowOverlay((event) => {
                this._onWillShowOverlay.fire(event);
            }), this.contentContainer.dropTarget.onWillShowOverlay((event) => {
                this._onWillShowOverlay.fire(new DockviewWillShowOverlayLocationEvent(event, {
                    kind: 'content',
                    panel: this.activePanel,
                    api: this._api,
                    group: this.groupPanel,
                    getData: getPanelData,
                }));
            }), this.contentContainer.pointerDropTarget.onWillShowOverlay((event) => {
                this._onWillShowOverlay.fire(new DockviewWillShowOverlayLocationEvent(event, {
                    kind: 'content',
                    panel: this.activePanel,
                    api: this._api,
                    group: this.groupPanel,
                    getData: getPanelData,
                }));
            }), this._onMove, this._onDidChange, this._onDidDrop, this._onWillDrop, this._onDidAddPanel, this._onDidRemovePanel, this._onDidActivePanelChange, this._onUnhandledDragOverEvent, this._onDidPanelTitleChange, this._onDidPanelParametersChange, this._onDidCreateTabGroup, this._onDidDestroyTabGroup, this._onDidAddPanelToTabGroup, this._onDidRemovePanelFromTabGroup, this._onDidTabGroupChange, this._onDidTabGroupCollapsedChange, this._onDidCreateTabGroup.event(() => {
                this._scheduleTabGroupUpdate();
            }), this._onDidDestroyTabGroup.event(() => {
                this._scheduleTabGroupUpdate();
            }), this._onDidAddPanelToTabGroup.event(() => {
                this._scheduleTabGroupUpdate();
            }), this._onDidRemovePanelFromTabGroup.event(() => {
                this._scheduleTabGroupUpdate();
            }), this._onDidTabGroupChange.event(() => {
                this._scheduleTabGroupUpdate();
            }), this._onDidTabGroupCollapsedChange.event(() => {
                this._scheduleTabGroupUpdate();
            }));
        }
        _scheduleTabGroupUpdate() {
            if (this._pendingTabGroupUpdate) {
                return;
            }
            this._pendingTabGroupUpdate = true;
            queueMicrotask(() => {
                this._pendingTabGroupUpdate = false;
                if (!this.isDisposed) {
                    this.tabsContainer.updateTabGroups();
                }
            });
        }
        createTabGroup(options) {
            var _a;
            const id = (_a = options === null || options === void 0 ? void 0 : options.id) !== null && _a !== void 0 ? _a : `tg-${this.id}-${this._tabGroupIdCounter++}`;
            const tabGroup = new TabGroup(id, {
                label: options === null || options === void 0 ? void 0 : options.label,
                color: options === null || options === void 0 ? void 0 : options.color,
                collapsed: options === null || options === void 0 ? void 0 : options.collapsed,
                componentParams: options === null || options === void 0 ? void 0 : options.componentParams,
            });
            this._tabGroups.push(tabGroup);
            this._tabGroupMap.set(id, tabGroup);
            this._tabGroupDisposables.set(id, new CompositeDisposable(tabGroup.onDidChange(() => {
                this._onDidTabGroupChange.fire({ tabGroup });
            }), tabGroup.onDidCollapseChange((isCollapsed) => {
                if (isCollapsed) {
                    this._handleGroupCollapse(tabGroup);
                }
                else {
                    this._handleGroupExpand(tabGroup);
                }
                this._onDidTabGroupCollapsedChange.fire({
                    tabGroup,
                });
            }), tabGroup.onDidDestroy(() => {
                this._removeTabGroupInternal(tabGroup);
            })));
            this._onDidCreateTabGroup.fire({ tabGroup });
            return tabGroup;
        }
        dissolveTabGroup(tabGroupId) {
            const tabGroup = this._tabGroupMap.get(tabGroupId);
            if (!tabGroup) {
                return;
            }
            // Remove all panels from the group (they stay in the flat panel list)
            const panelIds = [...tabGroup.panelIds];
            for (const panelId of panelIds) {
                tabGroup.removePanel(panelId);
                this._panelToTabGroup.delete(panelId);
                this._onDidRemovePanelFromTabGroup.fire({ tabGroup, panelId });
            }
            tabGroup.dispose();
        }
        addPanelToTabGroup(tabGroupId, panelId, index) {
            const tabGroup = this._tabGroupMap.get(tabGroupId);
            if (!tabGroup) {
                return;
            }
            // Ensure the panel actually exists in this group model
            if (!this._panels.some((p) => p.id === panelId)) {
                return;
            }
            // Remove from any existing group first
            const existingGroup = this.getTabGroupForPanel(panelId);
            if (existingGroup) {
                if (existingGroup.id === tabGroupId) {
                    return; // already in this group
                }
                this.removePanelFromTabGroup(panelId);
            }
            tabGroup.addPanel(panelId, index);
            this._panelToTabGroup.set(panelId, tabGroup);
            // Enforce contiguity: move the panel in the flat _panels array
            // to the correct global position matching its group-local index
            this._enforceContiguity(tabGroup, panelId);
            this._onDidAddPanelToTabGroup.fire({ tabGroup, panelId });
        }
        /**
         * Move a panel to a new index within its tab group.
         * Updates both the group's panelIds order and the flat _panels array.
         */
        movePanelWithinGroup(tabGroupId, panelId, newIndex) {
            const tabGroup = this._tabGroupMap.get(tabGroupId);
            if (!tabGroup || !tabGroup.containsPanel(panelId)) {
                return;
            }
            // Remove and re-add at new index within the group
            tabGroup.removePanel(panelId);
            tabGroup.addPanel(panelId, newIndex);
            // Re-enforce contiguity in the flat array
            this._enforceContiguity(tabGroup, panelId);
            this.tabsContainer.updateTabGroups();
        }
        /**
         * Move a panel from one tab group to another.
         */
        movePanelBetweenGroups(sourcePanelId, targetTabGroupId, targetIndex) {
            const sourceGroup = this._findTabGroupForPanel(sourcePanelId);
            const targetGroup = this._tabGroupMap.get(targetTabGroupId);
            if (!targetGroup) {
                return;
            }
            if (sourceGroup) {
                sourceGroup.removePanel(sourcePanelId);
                this._panelToTabGroup.delete(sourcePanelId);
                this._onDidRemovePanelFromTabGroup.fire({
                    tabGroup: sourceGroup,
                    panelId: sourcePanelId,
                });
                // Auto-destroy empty source group
                if (sourceGroup.isEmpty) {
                    sourceGroup.dispose();
                }
            }
            targetGroup.addPanel(sourcePanelId, targetIndex);
            this._panelToTabGroup.set(sourcePanelId, targetGroup);
            this._enforceContiguity(targetGroup, sourcePanelId);
            this._onDidAddPanelToTabGroup.fire({
                tabGroup: targetGroup,
                panelId: sourcePanelId,
            });
        }
        /**
         * Move an entire tab group to a new position in the tab bar.
         * The group's internal panel order is preserved.
         */
        moveTabGroup(tabGroupId, targetIndex) {
            const tabGroup = this._tabGroupMap.get(tabGroupId);
            if (!tabGroup || tabGroup.panelIds.length === 0) {
                return;
            }
            // Collect group panels in their current order
            const groupPanelIds = new Set(tabGroup.panelIds);
            const groupPanels = tabGroup.panelIds
                .map((pid) => this._panels.find((p) => p.id === pid))
                .filter((p) => p !== undefined);
            if (groupPanels.length === 0) {
                return;
            }
            // Count how many group panels sit before the target index so
            // we can compensate after removing them from the array.
            let groupPanelsBefore = 0;
            for (let i = 0; i < Math.min(targetIndex, this._panels.length); i++) {
                if (groupPanelIds.has(this._panels[i].id)) {
                    groupPanelsBefore++;
                }
            }
            // Remove group panels from the flat array
            for (const panel of groupPanels) {
                const idx = this._panels.indexOf(panel);
                if (idx !== -1) {
                    this._panels.splice(idx, 1);
                }
            }
            // Adjust target index to account for removed panels
            const adjustedIndex = targetIndex - groupPanelsBefore;
            // Clamp target index to valid range after removal
            const insertAt = Math.max(0, Math.min(adjustedIndex, this._panels.length));
            // Insert group panels at the target position
            this._panels.splice(insertAt, 0, ...groupPanels);
            // Rebuild the tabs container to match new order
            for (const panel of this._panels) {
                this.tabsContainer.delete(panel.id);
            }
            for (let i = 0; i < this._panels.length; i++) {
                this.tabsContainer.openPanel(this._panels[i], i);
            }
            this.tabsContainer.updateTabGroups();
        }
        /**
         * Ensure a panel is at the correct global index in _panels
         * to maintain contiguity of its tab group members.
         */
        _enforceContiguity(tabGroup, panelId) {
            const panel = this._panels.find((p) => p.id === panelId);
            if (!panel) {
                return;
            }
            const localIndex = tabGroup.indexOfPanel(panelId);
            const globalIndex = this._computeGlobalIndex(tabGroup, localIndex);
            const currentIndex = this._panels.indexOf(panel);
            if (currentIndex === globalIndex) {
                return;
            }
            // Move panel in the flat array
            this._panels.splice(currentIndex, 1);
            const adjustedIndex = globalIndex > currentIndex ? globalIndex - 1 : globalIndex;
            this._panels.splice(adjustedIndex, 0, panel);
            // Reorder in the tabs container to match
            this.tabsContainer.delete(panelId);
            this.tabsContainer.openPanel(panel, adjustedIndex);
        }
        /**
         * Compute the global index in _panels for a group-local index.
         * Finds where the group's panels start in the flat array and offsets.
         */
        _computeGlobalIndex(tabGroup, localIndex) {
            const groupPanelIds = tabGroup.panelIds;
            if (groupPanelIds.length <= 1) {
                // Only one panel (the one being added), keep current position
                const panel = this._panels.find((p) => p.id === groupPanelIds[0]);
                return panel ? this._panels.indexOf(panel) : this._panels.length;
            }
            // Find the first existing group member (other than the one at localIndex)
            // to anchor the group position
            for (let i = 0; i < groupPanelIds.length; i++) {
                if (i === localIndex) {
                    continue;
                }
                const existingPanel = this._panels.find((p) => p.id === groupPanelIds[i]);
                if (existingPanel) {
                    const existingGlobalIndex = this._panels.indexOf(existingPanel);
                    // Offset based on relative position within group
                    return Math.max(0, existingGlobalIndex + (localIndex - i));
                }
            }
            return this._panels.length;
        }
        removePanelFromTabGroup(panelId) {
            const tabGroup = this._findTabGroupForPanel(panelId);
            if (!tabGroup) {
                return;
            }
            tabGroup.removePanel(panelId);
            this._panelToTabGroup.delete(panelId);
            this._onDidRemovePanelFromTabGroup.fire({ tabGroup, panelId });
            // Auto-destroy empty groups
            if (tabGroup.isEmpty) {
                tabGroup.dispose();
            }
        }
        getTabGroups() {
            return this._tabGroups;
        }
        updateTabGroups() {
            this.tabsContainer.updateTabGroups();
        }
        refreshTabGroupAccent() {
            this.tabsContainer.refreshTabGroupAccent();
        }
        refreshWatermark() {
            var _a, _b;
            if (this.watermark) {
                this.watermark.element.remove();
                (_b = (_a = this.watermark).dispose) === null || _b === void 0 ? void 0 : _b.call(_a);
                this.watermark = undefined;
            }
            this.updateContainer();
        }
        getTabGroupForPanel(panelId) {
            return this._findTabGroupForPanel(panelId);
        }
        _findTabGroupForPanel(panelId) {
            return this._panelToTabGroup.get(panelId);
        }
        _removeTabGroupInternal(tabGroup) {
            const index = this._tabGroups.indexOf(tabGroup);
            if (index !== -1) {
                this._tabGroups.splice(index, 1);
                this._tabGroupMap.delete(tabGroup.id);
                for (const panelId of tabGroup.panelIds) {
                    this._panelToTabGroup.delete(panelId);
                }
                this._onDidDestroyTabGroup.fire({ tabGroup });
                // Dispose the external listeners (onDidChange, onDidCollapseChange)
                // we registered on this group. We cannot dispose synchronously
                // here because this method runs inside the onDidDestroy fire
                // loop — disposing the CompositeDisposable that holds the
                // onDidDestroy subscription would splice listeners mid-iteration.
                // Schedule cleanup on the next microtask instead.
                const tabGroupDisposable = this._tabGroupDisposables.get(tabGroup.id);
                this._tabGroupDisposables.delete(tabGroup.id);
                if (tabGroupDisposable) {
                    this._pendingMicrotaskDisposables.add(tabGroupDisposable);
                    queueMicrotask(() => {
                        this._pendingMicrotaskDisposables.delete(tabGroupDisposable);
                        tabGroupDisposable.dispose();
                    });
                }
            }
        }
        _handleGroupCollapse(tabGroup) {
            if (!this._activePanel) {
                return;
            }
            // Only act if the active panel belongs to the collapsed group
            if (!tabGroup.containsPanel(this._activePanel.id)) {
                return;
            }
            const activePanelIndex = this._panels.indexOf(this._activePanel);
            // Search right first, then left, for a visible (non-collapsed-group) panel
            for (let i = activePanelIndex + 1; i < this._panels.length; i++) {
                const candidate = this._panels[i];
                const candidateGroup = this._findTabGroupForPanel(candidate.id);
                if (!candidateGroup || !candidateGroup.collapsed) {
                    this.doSetActivePanel(candidate);
                    this.updateContainer();
                    return;
                }
            }
            for (let i = activePanelIndex - 1; i >= 0; i--) {
                const candidate = this._panels[i];
                const candidateGroup = this._findTabGroupForPanel(candidate.id);
                if (!candidateGroup || !candidateGroup.collapsed) {
                    this.doSetActivePanel(candidate);
                    this.updateContainer();
                    return;
                }
            }
            // All tabs are in collapsed groups — show watermark
            this.contentContainer.closePanel();
            this.doSetActivePanel(undefined);
            this.updateContainer();
        }
        _handleGroupExpand(tabGroup) {
            if (this._activePanel) {
                return;
            }
            // Watermark is showing because all groups were collapsed.
            // Activate the first panel in the newly expanded group.
            const firstPanelId = tabGroup.panelIds[0];
            if (firstPanelId) {
                const panel = this._panels.find((p) => p.id === firstPanelId);
                if (panel) {
                    this.doSetActivePanel(panel);
                    this.updateContainer();
                }
            }
        }
        /** Restore tab groups from serialized data (used by fromJSON) */
        restoreTabGroups(serializedGroups) {
            // Bump counter past any restored numeric suffixes to avoid ID collisions
            for (const data of serializedGroups) {
                const match = data.id.match(/-(\d+)$/);
                if (match) {
                    const num = parseInt(match[1], 10) + 1;
                    if (num > this._tabGroupIdCounter) {
                        this._tabGroupIdCounter = num;
                    }
                }
            }
            for (const data of serializedGroups) {
                const tabGroup = this.createTabGroup({
                    id: data.id,
                    label: data.label,
                    color: data.color,
                    componentParams: data.componentParams,
                });
                const concreteGroup = this._tabGroupMap.get(tabGroup.id);
                for (const panelId of data.panelIds) {
                    // Only add panels that actually exist in this group model
                    if (this._panels.some((p) => p.id === panelId)) {
                        tabGroup.addPanel(panelId);
                        this._panelToTabGroup.set(panelId, concreteGroup);
                        this._enforceContiguity(concreteGroup, panelId);
                    }
                }
                if (data.collapsed) {
                    tabGroup.collapse();
                }
                // Auto-destroy if no valid panels were added
                if (tabGroup.isEmpty) {
                    tabGroup.dispose();
                }
            }
        }
        focusContent() {
            this.contentContainer.element.focus();
        }
        set renderContainer(value) {
            this.panels.forEach((panel) => {
                this.renderContainer.detatch(panel);
            });
            this._overwriteRenderContainer = value;
            this.panels.forEach((panel) => {
                this.rerender(panel);
            });
        }
        get renderContainer() {
            var _a;
            return ((_a = this._overwriteRenderContainer) !== null && _a !== void 0 ? _a : this.accessor.overlayRenderContainer);
        }
        set dropTargetContainer(value) {
            this._overwriteDropTargetContainer = value;
        }
        get dropTargetContainer() {
            var _a;
            return ((_a = this._overwriteDropTargetContainer) !== null && _a !== void 0 ? _a : this.accessor.rootDropTargetContainer);
        }
        initialize() {
            if (this.options.panels) {
                this.options.panels.forEach((panel) => {
                    this.doAddPanel(panel);
                });
            }
            if (this.options.activePanel) {
                this.openPanel(this.options.activePanel);
            }
            // must be run after the constructor otherwise this.parent may not be
            // correctly initialized
            this.setActive(this.isActive, true);
            this.updateContainer();
            this.updateHeaderActions();
        }
        updateHeaderActions() {
            if (this.accessor.options.createRightHeaderActionComponent) {
                this._rightHeaderActions =
                    this.accessor.options.createRightHeaderActionComponent(this.groupPanel);
                this._rightHeaderActionsDisposable.value = this._rightHeaderActions;
                this._rightHeaderActions.init({
                    containerApi: this._api,
                    api: this.groupPanel.api,
                    group: this.groupPanel,
                });
                this.tabsContainer.setRightActionsElement(this._rightHeaderActions.element);
            }
            else {
                this._rightHeaderActions = undefined;
                this._rightHeaderActionsDisposable.dispose();
                this.tabsContainer.setRightActionsElement(undefined);
            }
            if (this.accessor.options.createLeftHeaderActionComponent) {
                this._leftHeaderActions =
                    this.accessor.options.createLeftHeaderActionComponent(this.groupPanel);
                this._leftHeaderActionsDisposable.value = this._leftHeaderActions;
                this._leftHeaderActions.init({
                    containerApi: this._api,
                    api: this.groupPanel.api,
                    group: this.groupPanel,
                });
                this.tabsContainer.setLeftActionsElement(this._leftHeaderActions.element);
            }
            else {
                this._leftHeaderActions = undefined;
                this._leftHeaderActionsDisposable.dispose();
                this.tabsContainer.setLeftActionsElement(undefined);
            }
            if (this.accessor.options.createPrefixHeaderActionComponent) {
                this._prefixHeaderActions =
                    this.accessor.options.createPrefixHeaderActionComponent(this.groupPanel);
                this._prefixHeaderActionsDisposable.value =
                    this._prefixHeaderActions;
                this._prefixHeaderActions.init({
                    containerApi: this._api,
                    api: this.groupPanel.api,
                    group: this.groupPanel,
                });
                this.tabsContainer.setPrefixActionsElement(this._prefixHeaderActions.element);
            }
            else {
                this._prefixHeaderActions = undefined;
                this._prefixHeaderActionsDisposable.dispose();
                this.tabsContainer.setPrefixActionsElement(undefined);
            }
        }
        rerender(panel) {
            this.contentContainer.renderPanel(panel, { asActive: false });
        }
        indexOf(panel) {
            return this.tabsContainer.indexOf(panel.id);
        }
        toJSON() {
            var _a;
            const result = {
                views: this.tabsContainer.panels,
                activeView: (_a = this._activePanel) === null || _a === void 0 ? void 0 : _a.id,
                id: this.id,
            };
            if (this.locked !== false) {
                result.locked = this.locked;
            }
            if (this.header.hidden) {
                result.hideHeader = true;
            }
            if (this.headerPosition !== 'top') {
                result.headerPosition = this.headerPosition;
            }
            if (this._tabGroups.length > 0) {
                result.tabGroups = this._tabGroups.map((tg) => tg.toJSON());
            }
            return result;
        }
        moveToNext(options) {
            if (!options) {
                options = {};
            }
            if (!options.panel) {
                options.panel = this.activePanel;
            }
            const index = options.panel ? this.panels.indexOf(options.panel) : -1;
            let normalizedIndex;
            if (index < this.panels.length - 1) {
                normalizedIndex = index + 1;
            }
            else if (!options.suppressRoll) {
                normalizedIndex = 0;
            }
            else {
                return;
            }
            this.openPanel(this.panels[normalizedIndex]);
        }
        moveToPrevious(options) {
            if (!options) {
                options = {};
            }
            if (!options.panel) {
                options.panel = this.activePanel;
            }
            if (!options.panel) {
                return;
            }
            const index = this.panels.indexOf(options.panel);
            let normalizedIndex;
            if (index > 0) {
                normalizedIndex = index - 1;
            }
            else if (!options.suppressRoll) {
                normalizedIndex = this.panels.length - 1;
            }
            else {
                return;
            }
            this.openPanel(this.panels[normalizedIndex]);
        }
        containsPanel(panel) {
            return this.panels.includes(panel);
        }
        init(_params) {
            //noop
        }
        update(_params) {
            //noop
        }
        focus() {
            var _a;
            (_a = this._activePanel) === null || _a === void 0 ? void 0 : _a.focus();
        }
        openPanel(panel, options = {}) {
            /**
             * set the panel group
             * add the panel
             * check if group active
             * check if panel active
             */
            if (typeof options.index !== 'number' ||
                options.index > this.panels.length) {
                options.index = this.panels.length;
            }
            const skipSetActive = !!options.skipSetActive;
            // ensure the group is updated before we fire any events
            panel.updateParentGroup(this.groupPanel, {
                skipSetActive: options.skipSetActive,
            });
            this.doAddPanel(panel, options.index, {
                skipSetActive: skipSetActive,
            });
            if (this._activePanel === panel) {
                this.contentContainer.renderPanel(panel, { asActive: true });
                return;
            }
            if (!skipSetActive) {
                this.doSetActivePanel(panel);
            }
            if (!options.skipSetGroupActive) {
                this.accessor.doSetGroupActive(this.groupPanel);
            }
            if (!options.skipSetActive) {
                this.updateContainer();
            }
        }
        removePanel(groupItemOrId, options = {
            skipSetActive: false,
        }) {
            const id = typeof groupItemOrId === 'string'
                ? groupItemOrId
                : groupItemOrId.id;
            const panelToRemove = this._panels.find((panel) => panel.id === id);
            if (!panelToRemove) {
                throw new Error('invalid operation');
            }
            return this._removePanel(panelToRemove, options);
        }
        closeAllPanels() {
            if (this.panels.length > 0) {
                // take a copy since we will be edting the array as we iterate through
                const arrPanelCpy = [...this.panels];
                for (const panel of arrPanelCpy) {
                    this.doClose(panel);
                }
            }
            else {
                this.accessor.removeGroup(this.groupPanel);
            }
        }
        closePanel(panel) {
            this.doClose(panel);
        }
        doClose(panel) {
            const isLast = this.panels.length === 1 && this.accessor.groups.length === 1;
            this.accessor.removePanel(panel, isLast && this.accessor.options.noPanelsOverlay === 'emptyGroup'
                ? { removeEmptyGroup: false }
                : undefined);
        }
        isPanelActive(panel) {
            return this._activePanel === panel;
        }
        updateActions(element) {
            this.tabsContainer.setRightActionsElement(element);
        }
        setActive(isGroupActive, force = false) {
            if (!force && this.isActive === isGroupActive) {
                return;
            }
            this._isGroupActive = isGroupActive;
            toggleClass(this.container, 'dv-active-group', isGroupActive);
            toggleClass(this.container, 'dv-inactive-group', !isGroupActive);
            this.tabsContainer.setActive(this.isActive);
            if (!this._activePanel && this.panels.length > 0) {
                const candidate = this._panels.find((p) => {
                    const tg = this._findTabGroupForPanel(p.id);
                    return !tg || !tg.collapsed;
                });
                if (candidate) {
                    this.doSetActivePanel(candidate);
                }
            }
            this.updateContainer();
        }
        layout(width, height) {
            var _a;
            this._width = width;
            this._height = height;
            this.contentContainer.layout(this._width, this._height);
            if ((_a = this._activePanel) === null || _a === void 0 ? void 0 : _a.layout) {
                this._activePanel.layout(this._width, this._height);
            }
        }
        _removePanel(panel, options) {
            const isActivePanel = this._activePanel === panel;
            this.doRemovePanel(panel);
            if (isActivePanel && this.panels.length > 0) {
                const nextPanel = this.mostRecentlyUsed[0];
                this.openPanel(nextPanel, {
                    skipSetActive: options.skipSetActive,
                    skipSetGroupActive: options.skipSetActiveGroup,
                });
            }
            if (this._activePanel && this.panels.length === 0) {
                this.doSetActivePanel(undefined);
            }
            if (!options.skipSetActive) {
                this.updateContainer();
            }
            return panel;
        }
        doRemovePanel(panel) {
            const index = this.panels.indexOf(panel);
            if (this._activePanel === panel) {
                this.contentContainer.closePanel();
            }
            this.tabsContainer.delete(panel.id);
            this._panels.splice(index, 1);
            if (this.mostRecentlyUsed.includes(panel)) {
                const index = this.mostRecentlyUsed.indexOf(panel);
                this.mostRecentlyUsed.splice(index, 1);
            }
            const disposable = this._panelDisposables.get(panel.id);
            if (disposable) {
                disposable.dispose();
                this._panelDisposables.delete(panel.id);
            }
            // Remove panel from its tab group (auto-destroys empty groups)
            this.removePanelFromTabGroup(panel.id);
            this._onDidRemovePanel.fire({ panel });
        }
        doAddPanel(panel, index = this.panels.length, options = { skipSetActive: false }) {
            const existingPanel = this._panels.indexOf(panel);
            const hasExistingPanel = existingPanel > -1;
            this.tabsContainer.show();
            this.contentContainer.show();
            this.tabsContainer.openPanel(panel, index);
            if (!options.skipSetActive) {
                this.contentContainer.openPanel(panel);
            }
            else if (panel.api.renderer === 'always') {
                this.contentContainer.renderPanel(panel, { asActive: false });
            }
            if (hasExistingPanel) {
                // TODO - need to ensure ordering hasn't changed and if it has need to re-order this.panels
                return;
            }
            this.updateMru(panel);
            this.panels.splice(index, 0, panel);
            this._panelDisposables.set(panel.id, new CompositeDisposable(panel.api.onDidTitleChange((event) => this._onDidPanelTitleChange.fire(event)), panel.api.onDidParametersChange((event) => this._onDidPanelParametersChange.fire(event))));
            this._onDidAddPanel.fire({ panel });
        }
        doSetActivePanel(panel) {
            if (this._activePanel === panel) {
                return;
            }
            this._activePanel = panel;
            if (panel) {
                this.tabsContainer.setActivePanel(panel);
                this.contentContainer.openPanel(panel);
                panel.layout(this._width, this._height);
                this.updateMru(panel);
                // Refresh focus state to handle programmatic activation without DOM focus change
                this.contentContainer.refreshFocusState();
                this._onDidActivePanelChange.fire({
                    panel,
                });
            }
        }
        updateMru(panel) {
            if (this.mostRecentlyUsed.includes(panel)) {
                this.mostRecentlyUsed.splice(this.mostRecentlyUsed.indexOf(panel), 1);
            }
            this.mostRecentlyUsed = [panel, ...this.mostRecentlyUsed];
        }
        updateContainer() {
            var _a, _b;
            this.panels.forEach((panel) => panel.runEvents());
            const shouldShowWatermark = this.isEmpty || !this._activePanel;
            if (shouldShowWatermark && !this.watermark) {
                const watermark = this.accessor.createWatermarkComponent();
                watermark.init({
                    containerApi: this._api,
                    group: this.groupPanel,
                });
                this.watermark = watermark;
                addDisposableListener(this.watermark.element, 'pointerdown', () => {
                    if (!this.isActive) {
                        this.accessor.doSetGroupActive(this.groupPanel);
                    }
                });
                this.contentContainer.element.appendChild(this.watermark.element);
            }
            if (!shouldShowWatermark && this.watermark) {
                this.watermark.element.remove();
                (_b = (_a = this.watermark).dispose) === null || _b === void 0 ? void 0 : _b.call(_a);
                this.watermark = undefined;
            }
        }
        canDisplayOverlay(event, position, target) {
            const firedEvent = new DockviewUnhandledDragOverEvent(event, target, position, getPanelData, this.accessor.getPanel(this.id));
            this._onUnhandledDragOverEvent.fire(firedEvent);
            return firedEvent.isAccepted;
        }
        handleDropEvent(type, event, position, index) {
            if (this.locked === 'no-drop-target') {
                return;
            }
            function getKind() {
                switch (type) {
                    case 'header':
                        return typeof index === 'number' ? 'tab' : 'header_space';
                    case 'content':
                        return 'content';
                }
            }
            const panel = typeof index === 'number' ? this.panels[index] : undefined;
            const willDropEvent = new DockviewWillDropEvent({
                nativeEvent: event,
                position,
                panel,
                getData: () => getPanelData(),
                kind: getKind(),
                group: this.groupPanel,
                api: this._api,
            });
            this._onWillDrop.fire(willDropEvent);
            if (willDropEvent.defaultPrevented) {
                return;
            }
            const data = getPanelData();
            if (data && data.viewId === this.accessor.id) {
                if (type === 'content') {
                    if (data.groupId === this.id) {
                        // don't allow to drop on self for center position
                        if (position === 'center') {
                            return;
                        }
                        if (data.panelId === null && !data.tabGroupId) {
                            // Full-group drops on self are a no-op.
                            // Tab-group drags are partial moves: an edge drop
                            // splits the layout and creates a new group.
                            return;
                        }
                    }
                }
                if (type === 'header') {
                    if (data.groupId === this.id) {
                        if (data.panelId === null && !data.tabGroupId) {
                            return;
                        }
                    }
                }
                if (data.panelId === null) {
                    // this is a group move dnd event
                    const { groupId } = data;
                    this._onMove.fire({
                        target: position,
                        groupId: groupId,
                        index,
                        tabGroupId: data.tabGroupId,
                    });
                    return;
                }
                const fromSameGroup = this.tabsContainer.indexOf(data.panelId) !== -1;
                if (fromSameGroup && this.tabsContainer.size === 1) {
                    return;
                }
                const { groupId, panelId } = data;
                const isSameGroup = this.id === groupId;
                if (isSameGroup && !position) {
                    const oldIndex = this.tabsContainer.indexOf(panelId);
                    if (oldIndex === index) {
                        return;
                    }
                }
                this._onMove.fire({
                    target: position,
                    groupId: data.groupId,
                    itemId: data.panelId,
                    index,
                });
            }
            else {
                this._onDidDrop.fire(new DockviewDidDropEvent({
                    nativeEvent: event,
                    position,
                    panel,
                    getData: () => getPanelData(),
                    group: this.groupPanel,
                    api: this._api,
                }));
            }
        }
        updateDragAndDropState() {
            this.tabsContainer.updateDragAndDropState();
        }
        dispose() {
            var _a, _b, _c;
            super.dispose();
            (_a = this.watermark) === null || _a === void 0 ? void 0 : _a.element.remove();
            (_c = (_b = this.watermark) === null || _b === void 0 ? void 0 : _b.dispose) === null || _c === void 0 ? void 0 : _c.call(_b);
            this.watermark = undefined;
            // Dispose all tab groups
            for (const tabGroup of [...this._tabGroups]) {
                tabGroup.dispose();
            }
            for (const disposable of this._tabGroupDisposables.values()) {
                disposable.dispose();
            }
            this._tabGroupDisposables.clear();
            // Dispose any microtask-deferred disposables that haven't run yet
            for (const disposable of this._pendingMicrotaskDisposables) {
                disposable.dispose();
            }
            this._pendingMicrotaskDisposables.clear();
            for (const panel of this.panels) {
                panel.dispose();
            }
            this.tabsContainer.dispose();
            this.contentContainer.dispose();
        }
    }

    class GridviewPanelApiImpl extends PanelApiImpl {
        constructor(id, component, panel) {
            super(id, component);
            this._onDidConstraintsChangeInternal = new Emitter();
            this.onDidConstraintsChangeInternal = this._onDidConstraintsChangeInternal.event;
            this._onDidConstraintsChange = new Emitter();
            this.onDidConstraintsChange = this._onDidConstraintsChange.event;
            this._onDidSizeChange = new Emitter();
            this.onDidSizeChange = this._onDidSizeChange.event;
            this.addDisposables(this._onDidConstraintsChangeInternal, this._onDidConstraintsChange, this._onDidSizeChange);
            if (panel) {
                this.initialize(panel);
            }
        }
        setConstraints(value) {
            this._onDidConstraintsChangeInternal.fire(value);
        }
        setSize(event) {
            this._onDidSizeChange.fire(event);
        }
    }

    class GridviewPanel extends BasePanelView {
        get priority() {
            return this._priority;
        }
        get snap() {
            return this._snap;
        }
        get minimumWidth() {
            /**
             * defer to protected function to allow subclasses to override easily.
             * see https://github.com/microsoft/TypeScript/issues/338
             */
            return this.__minimumWidth();
        }
        get minimumHeight() {
            /**
             * defer to protected function to allow subclasses to override easily.
             * see https://github.com/microsoft/TypeScript/issues/338
             */
            return this.__minimumHeight();
        }
        get maximumHeight() {
            /**
             * defer to protected function to allow subclasses to override easily.
             * see https://github.com/microsoft/TypeScript/issues/338
             */
            return this.__maximumHeight();
        }
        get maximumWidth() {
            /**
             * defer to protected function to allow subclasses to override easily.
             * see https://github.com/microsoft/TypeScript/issues/338
             */
            return this.__maximumWidth();
        }
        __minimumWidth() {
            const width = typeof this._minimumWidth === 'function'
                ? this._minimumWidth()
                : this._minimumWidth;
            if (width !== this._evaluatedMinimumWidth) {
                this._evaluatedMinimumWidth = width;
                this.updateConstraints();
            }
            return width;
        }
        __maximumWidth() {
            const width = typeof this._maximumWidth === 'function'
                ? this._maximumWidth()
                : this._maximumWidth;
            if (width !== this._evaluatedMaximumWidth) {
                this._evaluatedMaximumWidth = width;
                this.updateConstraints();
            }
            return width;
        }
        __minimumHeight() {
            const height = typeof this._minimumHeight === 'function'
                ? this._minimumHeight()
                : this._minimumHeight;
            if (height !== this._evaluatedMinimumHeight) {
                this._evaluatedMinimumHeight = height;
                this.updateConstraints();
            }
            return height;
        }
        __maximumHeight() {
            const height = typeof this._maximumHeight === 'function'
                ? this._maximumHeight()
                : this._maximumHeight;
            if (height !== this._evaluatedMaximumHeight) {
                this._evaluatedMaximumHeight = height;
                this.updateConstraints();
            }
            return height;
        }
        get isActive() {
            return this.api.isActive;
        }
        get isVisible() {
            return this.api.isVisible;
        }
        constructor(id, component, options, api) {
            super(id, component, api !== null && api !== void 0 ? api : new GridviewPanelApiImpl(id, component));
            this._evaluatedMinimumWidth = 0;
            this._evaluatedMaximumWidth = Number.MAX_SAFE_INTEGER;
            this._evaluatedMinimumHeight = 0;
            this._evaluatedMaximumHeight = Number.MAX_SAFE_INTEGER;
            this._minimumWidth = 0;
            this._minimumHeight = 0;
            this._maximumWidth = Number.MAX_SAFE_INTEGER;
            this._maximumHeight = Number.MAX_SAFE_INTEGER;
            this._snap = false;
            this._onDidChange = new Emitter();
            this.onDidChange = this._onDidChange.event;
            if (typeof (options === null || options === void 0 ? void 0 : options.minimumWidth) === 'number') {
                this._minimumWidth = options.minimumWidth;
            }
            if (typeof (options === null || options === void 0 ? void 0 : options.maximumWidth) === 'number') {
                this._maximumWidth = options.maximumWidth;
            }
            if (typeof (options === null || options === void 0 ? void 0 : options.minimumHeight) === 'number') {
                this._minimumHeight = options.minimumHeight;
            }
            if (typeof (options === null || options === void 0 ? void 0 : options.maximumHeight) === 'number') {
                this._maximumHeight = options.maximumHeight;
            }
            this.api.initialize(this); // TODO: required to by-pass 'super before this' requirement
            this.addDisposables(this.api.onWillVisibilityChange((event) => {
                const { isVisible } = event;
                const { accessor } = this._params;
                accessor.setVisible(this, isVisible);
            }), this.api.onActiveChange(() => {
                const { accessor } = this._params;
                accessor.doSetGroupActive(this);
            }), this.api.onDidConstraintsChangeInternal((event) => {
                if (typeof event.minimumWidth === 'number' ||
                    typeof event.minimumWidth === 'function') {
                    this._minimumWidth = event.minimumWidth;
                }
                if (typeof event.minimumHeight === 'number' ||
                    typeof event.minimumHeight === 'function') {
                    this._minimumHeight = event.minimumHeight;
                }
                if (typeof event.maximumWidth === 'number' ||
                    typeof event.maximumWidth === 'function') {
                    this._maximumWidth = event.maximumWidth;
                }
                if (typeof event.maximumHeight === 'number' ||
                    typeof event.maximumHeight === 'function') {
                    this._maximumHeight = event.maximumHeight;
                }
            }), this.api.onDidSizeChange((event) => {
                this._onDidChange.fire({
                    height: event.height,
                    width: event.width,
                });
            }), this._onDidChange);
        }
        setVisible(isVisible) {
            this.api._onDidVisibilityChange.fire({ isVisible });
        }
        setActive(isActive) {
            this.api._onDidActiveChange.fire({ isActive });
        }
        init(parameters) {
            if (parameters.maximumHeight) {
                this._maximumHeight = parameters.maximumHeight;
            }
            if (parameters.minimumHeight) {
                this._minimumHeight = parameters.minimumHeight;
            }
            if (parameters.maximumWidth) {
                this._maximumWidth = parameters.maximumWidth;
            }
            if (parameters.minimumWidth) {
                this._minimumWidth = parameters.minimumWidth;
            }
            this._priority = parameters.priority;
            this._snap = !!parameters.snap;
            super.init(parameters);
            if (typeof parameters.isVisible === 'boolean') {
                this.setVisible(parameters.isVisible);
            }
        }
        updateConstraints() {
            this.api._onDidConstraintsChange.fire({
                minimumWidth: this._evaluatedMinimumWidth,
                maximumWidth: this._evaluatedMaximumWidth,
                minimumHeight: this._evaluatedMinimumHeight,
                maximumHeight: this._evaluatedMaximumHeight,
            });
        }
        toJSON() {
            const state = super.toJSON();
            const maximum = (value) => value === Number.MAX_SAFE_INTEGER ? undefined : value;
            const minimum = (value) => (value <= 0 ? undefined : value);
            return Object.assign(Object.assign({}, state), { minimumHeight: minimum(this.minimumHeight), maximumHeight: maximum(this.maximumHeight), minimumWidth: minimum(this.minimumWidth), maximumWidth: maximum(this.maximumWidth), snap: this.snap, priority: this.priority });
        }
    }

    const NOT_INITIALIZED_MESSAGE = 'dockview: DockviewGroupPanelApiImpl not initialized';
    class DockviewGroupPanelApiImpl extends GridviewPanelApiImpl {
        get location() {
            if (!this._group) {
                throw new Error(NOT_INITIALIZED_MESSAGE);
            }
            return this._group.model.location;
        }
        get locked() {
            if (!this._group) {
                throw new Error(NOT_INITIALIZED_MESSAGE);
            }
            return this._group.locked;
        }
        set locked(value) {
            if (!this._group) {
                throw new Error(NOT_INITIALIZED_MESSAGE);
            }
            this._group.locked = value;
        }
        constructor(id, accessor) {
            super(id, '__dockviewgroup__');
            this.accessor = accessor;
            this._onDidLocationChange = new Emitter();
            this.onDidLocationChange = this._onDidLocationChange.event;
            this._onDidActivePanelChange = new Emitter();
            this.onDidActivePanelChange = this._onDidActivePanelChange.event;
            this._onDidCollapsedChange = new Emitter();
            this.onDidCollapsedChange = this._onDidCollapsedChange.event;
            this.addDisposables(this._onDidLocationChange, this._onDidActivePanelChange, this._onDidCollapsedChange, this._onDidVisibilityChange.event((event) => {
                // When becoming visible, apply any pending size change
                if (event.isVisible && this._pendingSize) {
                    super.setSize(this._pendingSize);
                    this._pendingSize = undefined;
                }
            }));
        }
        setSize(event) {
            // Always store the requested size
            this._pendingSize = Object.assign({}, event);
            // Apply the size change immediately
            super.setSize(event);
        }
        close() {
            if (!this._group) {
                return;
            }
            return this.accessor.removeGroup(this._group);
        }
        getWindow() {
            return this.location.type === 'popout'
                ? this.location.getWindow()
                : window;
        }
        setHeaderPosition(position) {
            if (!this._group) {
                throw new Error(NOT_INITIALIZED_MESSAGE);
            }
            this._group.model.headerPosition = position;
        }
        getHeaderPosition() {
            if (!this._group) {
                throw new Error(NOT_INITIALIZED_MESSAGE);
            }
            return this._group.model.headerPosition;
        }
        moveTo(options) {
            var _a, _b, _c, _d;
            if (!this._group) {
                throw new Error(NOT_INITIALIZED_MESSAGE);
            }
            const group = (_a = options.group) !== null && _a !== void 0 ? _a : this.accessor.addGroup({
                direction: positionToDirection((_b = options.position) !== null && _b !== void 0 ? _b : 'right'),
                skipSetActive: (_c = options.skipSetActive) !== null && _c !== void 0 ? _c : false,
            });
            this.accessor.moveGroupOrPanel({
                from: { groupId: this._group.id },
                to: {
                    group,
                    position: options.group
                        ? ((_d = options.position) !== null && _d !== void 0 ? _d : 'center')
                        : 'center',
                    index: options.index,
                },
                skipSetActive: options.skipSetActive,
            });
        }
        maximize() {
            if (!this._group) {
                throw new Error(NOT_INITIALIZED_MESSAGE);
            }
            if (this.location.type !== 'grid') {
                // only grid groups can be maximized
                return;
            }
            this.accessor.maximizeGroup(this._group);
        }
        isMaximized() {
            if (!this._group) {
                throw new Error(NOT_INITIALIZED_MESSAGE);
            }
            return this.accessor.isMaximizedGroup(this._group);
        }
        exitMaximized() {
            if (!this._group) {
                throw new Error(NOT_INITIALIZED_MESSAGE);
            }
            if (this.isMaximized()) {
                this.accessor.exitMaximizedGroup();
            }
        }
        collapse() {
            if (!this._group) {
                return;
            }
            this.accessor.setEdgeGroupCollapsed(this._group, true);
        }
        expand() {
            if (!this._group) {
                return;
            }
            this.accessor.setEdgeGroupCollapsed(this._group, false);
        }
        isCollapsed() {
            if (!this._group) {
                return false;
            }
            return this.accessor.isEdgeGroupCollapsed(this._group);
        }
        initialize(group) {
            this._group = group;
        }
    }

    const MINIMUM_DOCKVIEW_GROUP_PANEL_WIDTH = 100;
    const MINIMUM_DOCKVIEW_GROUP_PANEL_HEIGHT = 100;
    class DockviewGroupPanel extends GridviewPanel {
        get minimumWidth() {
            var _a;
            // Check for explicitly set group constraint first
            if (typeof this._explicitConstraints.minimumWidth === 'number') {
                return this._explicitConstraints.minimumWidth;
            }
            const activePanelMinimumWidth = (_a = this.activePanel) === null || _a === void 0 ? void 0 : _a.minimumWidth;
            if (typeof activePanelMinimumWidth === 'number') {
                return activePanelMinimumWidth;
            }
            return super.__minimumWidth();
        }
        get minimumHeight() {
            var _a;
            // Check for explicitly set group constraint first
            if (typeof this._explicitConstraints.minimumHeight === 'number') {
                return this._explicitConstraints.minimumHeight;
            }
            const activePanelMinimumHeight = (_a = this.activePanel) === null || _a === void 0 ? void 0 : _a.minimumHeight;
            if (typeof activePanelMinimumHeight === 'number') {
                return activePanelMinimumHeight;
            }
            return super.__minimumHeight();
        }
        get maximumWidth() {
            var _a;
            // Check for explicitly set group constraint first
            if (typeof this._explicitConstraints.maximumWidth === 'number') {
                return this._explicitConstraints.maximumWidth;
            }
            const activePanelMaximumWidth = (_a = this.activePanel) === null || _a === void 0 ? void 0 : _a.maximumWidth;
            if (typeof activePanelMaximumWidth === 'number') {
                return activePanelMaximumWidth;
            }
            return super.__maximumWidth();
        }
        get maximumHeight() {
            var _a;
            // Check for explicitly set group constraint first
            if (typeof this._explicitConstraints.maximumHeight === 'number') {
                return this._explicitConstraints.maximumHeight;
            }
            const activePanelMaximumHeight = (_a = this.activePanel) === null || _a === void 0 ? void 0 : _a.maximumHeight;
            if (typeof activePanelMaximumHeight === 'number') {
                return activePanelMaximumHeight;
            }
            return super.__maximumHeight();
        }
        get panels() {
            return this._model.panels;
        }
        get activePanel() {
            return this._model.activePanel;
        }
        get size() {
            return this._model.size;
        }
        get model() {
            return this._model;
        }
        get locked() {
            return this._model.locked;
        }
        set locked(value) {
            this._model.locked = value;
        }
        get header() {
            return this._model.header;
        }
        constructor(accessor, id, options) {
            var _a, _b, _c, _d, _e, _f;
            super(id, 'groupview_default', {
                minimumHeight: (_b = (_a = options.constraints) === null || _a === void 0 ? void 0 : _a.minimumHeight) !== null && _b !== void 0 ? _b : MINIMUM_DOCKVIEW_GROUP_PANEL_HEIGHT,
                minimumWidth: (_d = (_c = options.constraints) === null || _c === void 0 ? void 0 : _c.minimumWidth) !== null && _d !== void 0 ? _d : MINIMUM_DOCKVIEW_GROUP_PANEL_WIDTH,
                maximumHeight: (_e = options.constraints) === null || _e === void 0 ? void 0 : _e.maximumHeight,
                maximumWidth: (_f = options.constraints) === null || _f === void 0 ? void 0 : _f.maximumWidth,
            }, new DockviewGroupPanelApiImpl(id, accessor));
            // Track explicitly set constraints to override panel constraints
            this._explicitConstraints = {};
            this.api.initialize(this); // cannot use 'this' after after 'super' call
            this._model = new DockviewGroupPanelModel(this.element, accessor, id, options, this);
            this.addDisposables(this.model.onDidActivePanelChange((event) => {
                this.api._onDidActivePanelChange.fire(event);
            }), this.api.onDidConstraintsChangeInternal((event) => {
                // Track explicitly set constraints to override panel constraints
                // Extract numeric values from functions or values
                if (event.minimumWidth !== undefined) {
                    this._explicitConstraints.minimumWidth =
                        typeof event.minimumWidth === 'function'
                            ? event.minimumWidth()
                            : event.minimumWidth;
                }
                if (event.minimumHeight !== undefined) {
                    this._explicitConstraints.minimumHeight =
                        typeof event.minimumHeight === 'function'
                            ? event.minimumHeight()
                            : event.minimumHeight;
                }
                if (event.maximumWidth !== undefined) {
                    this._explicitConstraints.maximumWidth =
                        typeof event.maximumWidth === 'function'
                            ? event.maximumWidth()
                            : event.maximumWidth;
                }
                if (event.maximumHeight !== undefined) {
                    this._explicitConstraints.maximumHeight =
                        typeof event.maximumHeight === 'function'
                            ? event.maximumHeight()
                            : event.maximumHeight;
                }
            }));
        }
        focus() {
            if (!this.api.isActive) {
                this.api.setActive();
            }
            super.focus();
        }
        initialize() {
            this._model.initialize();
        }
        setActive(isActive) {
            super.setActive(isActive);
            this.model.setActive(isActive);
        }
        layout(width, height) {
            super.layout(width, height);
            this.model.layout(width, height);
        }
        getComponent() {
            return this._model;
        }
        toJSON() {
            return this.model.toJSON();
        }
    }

    const themeDark = {
        name: 'dark',
        className: 'dockview-theme-dark',
        colorScheme: 'dark',
    };
    const themeLight = {
        name: 'light',
        className: 'dockview-theme-light',
        colorScheme: 'light',
    };
    const themeVisualStudio = {
        name: 'visualStudio',
        className: 'dockview-theme-vs',
        colorScheme: 'dark',
        // --dv-tabs-and-actions-container-height is 20px, but the VS theme applies
        // box-sizing: content-box + border-bottom: 2px, so the rendered strip is 22px.
        edgeGroupCollapsedSize: 22,
    };
    const themeAbyss = {
        name: 'abyss',
        className: 'dockview-theme-abyss',
        colorScheme: 'dark',
        tabGroupIndicator: 'none',
    };
    const themeDracula = {
        name: 'dracula',
        className: 'dockview-theme-dracula',
        colorScheme: 'dark',
    };
    const themeAbyssSpaced = {
        name: 'abyssSpaced',
        className: 'dockview-theme-abyss-spaced',
        colorScheme: 'dark',
        gap: 10,
        edgeGroupCollapsedSize: 44,
        dndOverlayMounting: 'absolute',
        dndPanelOverlay: 'group',
        dndTabIndicator: 'line',
        dndOverlayBorder: '2px solid var(--dv-active-sash-color)',
    };
    const themeLightSpaced = {
        name: 'lightSpaced',
        className: 'dockview-theme-light-spaced',
        colorScheme: 'light',
        gap: 10,
        edgeGroupCollapsedSize: 44,
        dndOverlayMounting: 'absolute',
        dndPanelOverlay: 'group',
        dndTabIndicator: 'line',
        dndOverlayBorder: '2px solid var(--dv-active-sash-color)',
    };
    const themeNord = {
        name: 'nord',
        className: 'dockview-theme-nord',
        colorScheme: 'dark',
    };
    const themeNordSpaced = {
        name: 'nordSpaced',
        className: 'dockview-theme-nord-spaced',
        colorScheme: 'dark',
        gap: 10,
        edgeGroupCollapsedSize: 44,
        dndOverlayMounting: 'absolute',
        dndPanelOverlay: 'group',
        dndTabIndicator: 'line',
        dndOverlayBorder: '2px solid var(--dv-active-sash-color)',
    };
    const themeCatppuccinMocha = {
        name: 'catppuccinMocha',
        className: 'dockview-theme-catppuccin-mocha',
        colorScheme: 'dark',
    };
    const themeCatppuccinMochaSpaced = {
        name: 'catppuccinMochaSpaced',
        className: 'dockview-theme-catppuccin-mocha-spaced',
        colorScheme: 'dark',
        gap: 10,
        edgeGroupCollapsedSize: 44,
        dndOverlayMounting: 'absolute',
        dndPanelOverlay: 'group',
        dndTabIndicator: 'line',
        dndOverlayBorder: '2px solid var(--dv-active-sash-color)',
    };
    const themeMonokai = {
        name: 'monokai',
        className: 'dockview-theme-monokai',
        colorScheme: 'dark',
    };
    const themeSolarizedLight = {
        name: 'solarizedLight',
        className: 'dockview-theme-solarized-light',
        colorScheme: 'light',
    };
    const themeSolarizedLightSpaced = {
        name: 'solarizedLightSpaced',
        className: 'dockview-theme-solarized-light-spaced',
        colorScheme: 'light',
        gap: 10,
        edgeGroupCollapsedSize: 44,
        dndOverlayMounting: 'absolute',
        dndPanelOverlay: 'group',
        dndTabIndicator: 'line',
        dndOverlayBorder: '2px solid var(--dv-active-sash-color)',
    };
    const themeGithubDark = {
        name: 'githubDark',
        className: 'dockview-theme-github-dark',
        colorScheme: 'dark',
    };
    const themeGithubDarkSpaced = {
        name: 'githubDarkSpaced',
        className: 'dockview-theme-github-dark-spaced',
        colorScheme: 'dark',
        gap: 10,
        edgeGroupCollapsedSize: 44,
        dndOverlayMounting: 'absolute',
        dndPanelOverlay: 'group',
        dndTabIndicator: 'line',
        dndOverlayBorder: '2px solid var(--dv-active-sash-color)',
    };
    const themeGithubLight = {
        name: 'githubLight',
        className: 'dockview-theme-github-light',
        colorScheme: 'light',
    };
    const themeGithubLightSpaced = {
        name: 'githubLightSpaced',
        className: 'dockview-theme-github-light-spaced',
        colorScheme: 'light',
        gap: 10,
        edgeGroupCollapsedSize: 44,
        dndOverlayMounting: 'absolute',
        dndPanelOverlay: 'group',
        dndTabIndicator: 'line',
        dndOverlayBorder: '2px solid var(--dv-active-sash-color)',
    };

    class DockviewPanelApiImpl extends GridviewPanelApiImpl {
        get location() {
            return this.group.api.location;
        }
        get title() {
            return this.panel.title;
        }
        get isGroupActive() {
            return this.group.isActive;
        }
        get renderer() {
            return this.panel.renderer;
        }
        set group(value) {
            const oldGroup = this._group;
            if (this._group !== value) {
                this._group = value;
                this._onDidGroupChange.fire({});
                this.setupGroupEventListeners(oldGroup);
                this._onDidLocationChange.fire({
                    location: this.group.api.location,
                });
            }
        }
        get group() {
            return this._group;
        }
        get tabComponent() {
            return this._tabComponent;
        }
        constructor(panel, group, accessor, component, tabComponent) {
            super(panel.id, component);
            this.panel = panel;
            this.accessor = accessor;
            this._onDidTitleChange = new Emitter();
            this.onDidTitleChange = this._onDidTitleChange.event;
            this._onDidActiveGroupChange = new Emitter();
            this.onDidActiveGroupChange = this._onDidActiveGroupChange.event;
            this._onDidGroupChange = new Emitter();
            this.onDidGroupChange = this._onDidGroupChange.event;
            this._onDidRendererChange = new Emitter();
            this.onDidRendererChange = this._onDidRendererChange.event;
            this._onDidLocationChange = new Emitter();
            this.onDidLocationChange = this._onDidLocationChange.event;
            this.groupEventsDisposable = new MutableDisposable();
            this._tabComponent = tabComponent;
            this.initialize(panel);
            this._group = group;
            this.setupGroupEventListeners();
            this.addDisposables(this.groupEventsDisposable, this._onDidRendererChange, this._onDidTitleChange, this._onDidGroupChange, this._onDidActiveGroupChange, this._onDidLocationChange);
        }
        getWindow() {
            return this.group.api.getWindow();
        }
        moveTo(options) {
            var _a, _b;
            this.accessor.moveGroupOrPanel({
                from: { groupId: this._group.id, panelId: this.panel.id },
                to: {
                    group: (_a = options.group) !== null && _a !== void 0 ? _a : this._group,
                    position: options.group
                        ? ((_b = options.position) !== null && _b !== void 0 ? _b : 'center')
                        : 'center',
                    index: options.index,
                },
                skipSetActive: options.skipSetActive,
            });
        }
        setTitle(title) {
            this.panel.setTitle(title);
        }
        setRenderer(renderer) {
            this.panel.setRenderer(renderer);
        }
        close() {
            this.group.model.closePanel(this.panel);
        }
        maximize() {
            this.group.api.maximize();
        }
        isMaximized() {
            return this.group.api.isMaximized();
        }
        exitMaximized() {
            this.group.api.exitMaximized();
        }
        setupGroupEventListeners(previousGroup) {
            var _a;
            let _trackGroupActive = (_a = previousGroup === null || previousGroup === void 0 ? void 0 : previousGroup.isActive) !== null && _a !== void 0 ? _a : false; // prevent duplicate events with same state
            this.groupEventsDisposable.value = new CompositeDisposable(this.group.api.onDidVisibilityChange((event) => {
                const hasBecomeHidden = !event.isVisible && this.isVisible;
                const hasBecomeVisible = event.isVisible && !this.isVisible;
                const isActivePanel = this.group.model.isPanelActive(this.panel);
                if (hasBecomeHidden || (hasBecomeVisible && isActivePanel)) {
                    this._onDidVisibilityChange.fire(event);
                }
            }), this.group.api.onDidLocationChange((event) => {
                if (this.group !== this.panel.group) {
                    return;
                }
                this._onDidLocationChange.fire(event);
            }), this.group.api.onDidActiveChange(() => {
                if (this.group !== this.panel.group) {
                    return;
                }
                if (_trackGroupActive !== this.isGroupActive) {
                    _trackGroupActive = this.isGroupActive;
                    this._onDidActiveGroupChange.fire({
                        isActive: this.isGroupActive,
                    });
                }
            }));
        }
    }

    class DockviewPanel extends CompositeDisposable {
        get params() {
            return this._params;
        }
        get title() {
            return this._title;
        }
        get group() {
            return this._group;
        }
        get renderer() {
            var _a;
            return (_a = this._renderer) !== null && _a !== void 0 ? _a : this.accessor.renderer;
        }
        get minimumWidth() {
            return this._minimumWidth;
        }
        get minimumHeight() {
            return this._minimumHeight;
        }
        get maximumWidth() {
            return this._maximumWidth;
        }
        get maximumHeight() {
            return this._maximumHeight;
        }
        constructor(id, component, tabComponent, accessor, containerApi, group, view, options) {
            super();
            this.id = id;
            this.accessor = accessor;
            this.containerApi = containerApi;
            this.view = view;
            this._renderer = options.renderer;
            this._group = group;
            this._minimumWidth = options.minimumWidth;
            this._minimumHeight = options.minimumHeight;
            this._maximumWidth = options.maximumWidth;
            this._maximumHeight = options.maximumHeight;
            this.api = new DockviewPanelApiImpl(this, this._group, accessor, component, tabComponent);
            this.addDisposables(this.api.onActiveChange(() => {
                accessor.setActivePanel(this);
            }), this.api.onDidSizeChange((event) => {
                // forward the resize event to the group since if you want to resize a panel
                // you are actually just resizing the panels parent which is the group
                this.group.api.setSize(event);
            }), this.api.onDidRendererChange(() => {
                this.group.model.rerender(this);
            }));
        }
        init(params) {
            this._params = params.params;
            this.view.init(Object.assign(Object.assign({}, params), { api: this.api, containerApi: this.containerApi }));
            this.setTitle(params.title);
        }
        focus() {
            const event = new WillFocusEvent();
            this.api._onWillFocus.fire(event);
            if (event.defaultPrevented) {
                return;
            }
            if (!this.api.isActive) {
                this.api.setActive();
            }
        }
        toJSON() {
            return {
                id: this.id,
                contentComponent: this.view.contentComponent,
                tabComponent: this.view.tabComponent,
                params: Object.keys(this._params || {}).length > 0
                    ? this._params
                    : undefined,
                title: this.title,
                renderer: this._renderer,
                minimumHeight: this._minimumHeight,
                maximumHeight: this._maximumHeight,
                minimumWidth: this._minimumWidth,
                maximumWidth: this._maximumWidth,
            };
        }
        setTitle(title) {
            const didTitleChange = title !== this.title;
            if (didTitleChange) {
                this._title = title;
                // keep the view-model's cached init params in sync so that tab
                // renderers constructed lazily (e.g. the header overflow
                // dropdown via createTabRenderer) see the updated title
                // (#914).
                this.view.setTitle(title);
                this.api._onDidTitleChange.fire({ title });
            }
        }
        setRenderer(renderer) {
            const didChange = renderer !== this.renderer;
            if (didChange) {
                this._renderer = renderer;
                this.api._onDidRendererChange.fire({
                    renderer: renderer,
                });
            }
        }
        update(event) {
            var _a;
            // merge the new parameters with the existing parameters
            this._params = Object.assign(Object.assign({}, ((_a = this._params) !== null && _a !== void 0 ? _a : {})), event.params);
            /**
             * delete new keys that have a value of undefined,
             * allow values of null
             */
            for (const key of Object.keys(event.params)) {
                if (event.params[key] === undefined) {
                    delete this._params[key];
                }
            }
            // update the view with the updated props
            this.view.update({
                params: this._params,
            });
        }
        updateFromStateModel(state) {
            var _a, _b, _c;
            this._maximumHeight = state.maximumHeight;
            this._minimumHeight = state.minimumHeight;
            this._maximumWidth = state.maximumWidth;
            this._minimumWidth = state.minimumWidth;
            this.update({ params: (_a = state.params) !== null && _a !== void 0 ? _a : {} });
            this.setTitle((_b = state.title) !== null && _b !== void 0 ? _b : this.id);
            this.setRenderer((_c = state.renderer) !== null && _c !== void 0 ? _c : this.accessor.renderer);
            // state.contentComponent;
            // state.tabComponent;
        }
        updateParentGroup(group, options) {
            this._group = group;
            this.api.group = this._group;
            const isPanelVisible = this._group.model.isPanelActive(this);
            const isActive = this.group.api.isActive && isPanelVisible;
            if (!(options === null || options === void 0 ? void 0 : options.skipSetActive)) {
                if (this.api.isActive !== isActive) {
                    this.api._onDidActiveChange.fire({
                        isActive: this.group.api.isActive && isPanelVisible,
                    });
                }
            }
            if (this.api.isVisible !== isPanelVisible) {
                this.api._onDidVisibilityChange.fire({
                    isVisible: isPanelVisible,
                });
            }
        }
        runEvents() {
            const isPanelVisible = this._group.model.isPanelActive(this);
            const isActive = this.group.api.isActive && isPanelVisible;
            if (this.api.isActive !== isActive) {
                this.api._onDidActiveChange.fire({
                    isActive: this.group.api.isActive && isPanelVisible,
                });
            }
            if (this.api.isVisible !== isPanelVisible) {
                this.api._onDidVisibilityChange.fire({
                    isVisible: isPanelVisible,
                });
            }
        }
        layout(width, height) {
            // TODO: Can we somehow do height without header height or indicate what the header height is?
            this.api._onDidDimensionChange.fire({
                width,
                height: height,
            });
            this.view.layout(width, height);
        }
        dispose() {
            this.api.dispose();
            this.view.dispose();
        }
    }

    class DefaultTab extends CompositeDisposable {
        get element() {
            return this._element;
        }
        constructor() {
            super();
            this._element = document.createElement('div');
            this._element.className = 'dv-default-tab';
            this._content = document.createElement('div');
            this._content.className = 'dv-default-tab-content';
            this.action = document.createElement('div');
            this.action.className = 'dv-default-tab-action';
            this.action.appendChild(createCloseButton());
            this._element.appendChild(this._content);
            this._element.appendChild(this.action);
            this.render();
        }
        init(params) {
            this._title = params.title;
            this.addDisposables(params.api.onDidTitleChange((event) => {
                this._title = event.title;
                this.render();
            }), addDisposableListener(this.action, 'pointerdown', (ev) => {
                ev.preventDefault();
            }), addDisposableListener(this.action, 'click', (ev) => {
                if (ev.defaultPrevented) {
                    return;
                }
                ev.preventDefault();
                params.api.close();
            }));
            this.render();
        }
        render() {
            var _a;
            if (this._content.textContent !== this._title) {
                this._content.textContent = (_a = this._title) !== null && _a !== void 0 ? _a : '';
            }
        }
    }

    class DockviewPanelModel {
        get content() {
            return this._content;
        }
        get tab() {
            return this._tab;
        }
        constructor(accessor, id, contentComponent, tabComponent) {
            this.accessor = accessor;
            this.id = id;
            this.contentComponent = contentComponent;
            this.tabComponent = tabComponent;
            this._content = this.createContentComponent(this.id, contentComponent);
            this._tab = this.createTabComponent(this.id, tabComponent);
        }
        createTabRenderer(tabLocation) {
            var _a;
            const cmp = this.createTabComponent(this.id, this.tabComponent);
            if (this._params) {
                cmp.init(Object.assign(Object.assign({}, this._params), { tabLocation }));
            }
            if (this._updateEvent) {
                (_a = cmp.update) === null || _a === void 0 ? void 0 : _a.call(cmp, this._updateEvent);
            }
            return cmp;
        }
        init(params) {
            this._params = params;
            this.content.init(params);
            this.tab.init(Object.assign(Object.assign({}, params), { tabLocation: 'header' }));
        }
        setTitle(title) {
            // keep the cached init params in sync so that tab renderers created
            // lazily after the title changes (e.g. for the header overflow
            // dropdown) see the current title rather than the stale original.
            if (this._params) {
                this._params.title = title;
            }
        }
        layout(width, height) {
            var _a, _b;
            (_b = (_a = this.content).layout) === null || _b === void 0 ? void 0 : _b.call(_a, width, height);
        }
        update(event) {
            var _a, _b, _c, _d;
            this._updateEvent = event;
            (_b = (_a = this.content).update) === null || _b === void 0 ? void 0 : _b.call(_a, event);
            (_d = (_c = this.tab).update) === null || _d === void 0 ? void 0 : _d.call(_c, event);
        }
        dispose() {
            var _a, _b, _c, _d;
            (_b = (_a = this.content).dispose) === null || _b === void 0 ? void 0 : _b.call(_a);
            (_d = (_c = this.tab).dispose) === null || _d === void 0 ? void 0 : _d.call(_c);
        }
        createContentComponent(id, componentName) {
            return this.accessor.options.createComponent({
                id,
                name: componentName,
            });
        }
        createTabComponent(id, componentName) {
            const name = componentName !== null && componentName !== void 0 ? componentName : this.accessor.options.defaultTabComponent;
            if (name) {
                if (this.accessor.options.createTabComponent) {
                    const component = this.accessor.options.createTabComponent({
                        id,
                        name,
                    });
                    if (component) {
                        return component;
                    }
                    else {
                        return new DefaultTab();
                    }
                }
                console.warn(`dockview: tabComponent '${componentName}' was not found. falling back to the default tab.`);
            }
            return new DefaultTab();
        }
    }

    class DefaultDockviewDeserialzier {
        constructor(accessor) {
            this.accessor = accessor;
        }
        fromJSON(panelData, group) {
            var _a, _b;
            const panelId = panelData.id;
            const params = panelData.params;
            const title = panelData.title;
            const viewData = panelData.view;
            const contentComponent = viewData
                ? viewData.content.id
                : ((_a = panelData.contentComponent) !== null && _a !== void 0 ? _a : 'unknown');
            const tabComponent = viewData
                ? (_b = viewData.tab) === null || _b === void 0 ? void 0 : _b.id
                : panelData.tabComponent;
            const view = new DockviewPanelModel(this.accessor, panelId, contentComponent, tabComponent);
            const panel = new DockviewPanel(panelId, contentComponent, tabComponent, this.accessor, new DockviewApi(this.accessor), group, view, {
                renderer: panelData.renderer,
                minimumWidth: panelData.minimumWidth,
                minimumHeight: panelData.minimumHeight,
                maximumWidth: panelData.maximumWidth,
                maximumHeight: panelData.maximumHeight,
            });
            panel.init({
                title: title !== null && title !== void 0 ? title : panelId,
                params: params !== null && params !== void 0 ? params : {},
            });
            return panel;
        }
    }

    class Watermark extends CompositeDisposable {
        get element() {
            return this._element;
        }
        constructor() {
            super();
            this._element = document.createElement('div');
            this._element.className = 'dv-watermark';
        }
        init(_params) {
            // noop
        }
    }

    class AriaLevelTracker {
        constructor() {
            this._orderedList = [];
        }
        push(element) {
            this._orderedList = [
                ...this._orderedList.filter((item) => item !== element),
                element,
            ];
            this.update();
        }
        destroy(element) {
            this._orderedList = this._orderedList.filter((item) => item !== element);
            this.update();
        }
        update() {
            for (let i = 0; i < this._orderedList.length; i++) {
                this._orderedList[i].setAttribute('aria-level', `${i}`);
                this._orderedList[i].style.zIndex =
                    `calc(var(--dv-overlay-z-index, 999) + ${i * 2})`;
            }
        }
    }
    const arialLevelTracker = new AriaLevelTracker();
    class Overlay extends CompositeDisposable {
        set minimumInViewportWidth(value) {
            this.options.minimumInViewportWidth = value;
        }
        set minimumInViewportHeight(value) {
            this.options.minimumInViewportHeight = value;
        }
        get element() {
            return this._element;
        }
        get isVisible() {
            return this._isVisible;
        }
        constructor(options) {
            super();
            this.options = options;
            this._element = document.createElement('div');
            this._onDidChange = new Emitter();
            this.onDidChange = this._onDidChange.event;
            this._onDidChangeEnd = new Emitter();
            this.onDidChangeEnd = this._onDidChangeEnd.event;
            this._onDidStartMoving = new Emitter();
            /** Fires once per drag, the first time the float actually moves. */
            this.onDidStartMoving = this._onDidStartMoving.event;
            this._dragMove = new MutableDisposable();
            this._dragCancelled = false;
            this.addDisposables(this._onDidChange, this._onDidChangeEnd, this._onDidStartMoving, this._dragMove);
            this._element.className = 'dv-resize-container';
            this._isVisible = true;
            this.setupResize('top');
            this.setupResize('bottom');
            this.setupResize('left');
            this.setupResize('right');
            this.setupResize('topleft');
            this.setupResize('topright');
            this.setupResize('bottomleft');
            this.setupResize('bottomright');
            this._element.appendChild(this.options.content);
            this.options.container.appendChild(this._element);
            // if input bad resize within acceptable boundaries
            this.setBounds(Object.assign(Object.assign(Object.assign(Object.assign({ height: this.options.height, width: this.options.width }, ('top' in this.options && { top: this.options.top })), ('bottom' in this.options && { bottom: this.options.bottom })), ('left' in this.options && { left: this.options.left })), ('right' in this.options && { right: this.options.right })));
            arialLevelTracker.push(this._element);
        }
        setVisible(isVisible) {
            if (isVisible === this.isVisible) {
                return;
            }
            this._isVisible = isVisible;
            toggleClass(this.element, 'dv-hidden', !this.isVisible);
        }
        bringToFront() {
            arialLevelTracker.push(this._element);
        }
        setBounds(bounds = {}) {
            if (typeof bounds.height === 'number') {
                this._element.style.height = `${bounds.height}px`;
            }
            if (typeof bounds.width === 'number') {
                this._element.style.width = `${bounds.width}px`;
            }
            if ('top' in bounds && typeof bounds.top === 'number') {
                this._element.style.top = `${bounds.top}px`;
                this._element.style.bottom = 'auto';
                this.verticalAlignment = 'top';
            }
            if ('bottom' in bounds && typeof bounds.bottom === 'number') {
                this._element.style.bottom = `${bounds.bottom}px`;
                this._element.style.top = 'auto';
                this.verticalAlignment = 'bottom';
            }
            if ('left' in bounds && typeof bounds.left === 'number') {
                this._element.style.left = `${bounds.left}px`;
                this._element.style.right = 'auto';
                this.horiziontalAlignment = 'left';
            }
            if ('right' in bounds && typeof bounds.right === 'number') {
                this._element.style.right = `${bounds.right}px`;
                this._element.style.left = 'auto';
                this.horiziontalAlignment = 'right';
            }
            const containerRect = this.options.container.getBoundingClientRect();
            const overlayRect = this._element.getBoundingClientRect();
            // region: ensure bounds within allowable limits
            // a minimum width of minimumViewportWidth must be inside the viewport
            const xOffset = Math.max(0, this.getMinimumWidth(overlayRect.width));
            // a minimum height of minimumViewportHeight must be inside the viewport
            const yOffset = Math.max(0, this.getMinimumHeight(overlayRect.height));
            if (this.verticalAlignment === 'top') {
                const top = clamp(overlayRect.top - containerRect.top, -yOffset, Math.max(0, containerRect.height - overlayRect.height + yOffset));
                this._element.style.top = `${top}px`;
                this._element.style.bottom = 'auto';
            }
            if (this.verticalAlignment === 'bottom') {
                const bottom = clamp(containerRect.bottom - overlayRect.bottom, -yOffset, Math.max(0, containerRect.height - overlayRect.height + yOffset));
                this._element.style.bottom = `${bottom}px`;
                this._element.style.top = 'auto';
            }
            if (this.horiziontalAlignment === 'left') {
                const left = clamp(overlayRect.left - containerRect.left, -xOffset, Math.max(0, containerRect.width - overlayRect.width + xOffset));
                this._element.style.left = `${left}px`;
                this._element.style.right = 'auto';
            }
            if (this.horiziontalAlignment === 'right') {
                const right = clamp(containerRect.right - overlayRect.right, -xOffset, Math.max(0, containerRect.width - overlayRect.width + xOffset));
                this._element.style.right = `${right}px`;
                this._element.style.left = 'auto';
            }
            this._onDidChange.fire();
        }
        toJSON() {
            const container = this.options.container.getBoundingClientRect();
            const element = this._element.getBoundingClientRect();
            const result = {};
            if (this.verticalAlignment === 'top') {
                result.top = parseFloat(this._element.style.top);
            }
            else if (this.verticalAlignment === 'bottom') {
                result.bottom = parseFloat(this._element.style.bottom);
            }
            else {
                result.top = element.top - container.top;
            }
            if (this.horiziontalAlignment === 'left') {
                result.left = parseFloat(this._element.style.left);
            }
            else if (this.horiziontalAlignment === 'right') {
                result.right = parseFloat(this._element.style.right);
            }
            else {
                result.left = element.left - container.left;
            }
            result.width = element.width;
            result.height = element.height;
            return result;
        }
        /**
         * Abort an in-flight move-the-float drag. Used by the void container
         * when a redock long-press fires after the move started, so the ghost
         * gesture wins without the float continuing to follow the finger.
         * Does not emit `onDidChangeEnd` because no change is being committed.
         */
        cancelPendingDrag() {
            if (!this._dragMove.value) {
                return;
            }
            this._dragCancelled = true;
            toggleClass(this._element, 'dv-resize-container-dragging', false);
            this._dragMove.value = exports.DockviewDisposable.NONE;
        }
        setupDrag(dragTarget, options = { inDragMode: false }) {
            const track = (captureTarget, pointerId) => {
                let offset = null;
                let hasMoved = false;
                this._dragCancelled = false;
                const iframes = disableIframePointEvents();
                if (captureTarget &&
                    typeof pointerId === 'number' &&
                    typeof captureTarget.setPointerCapture === 'function') {
                    try {
                        captureTarget.setPointerCapture(pointerId);
                    }
                    catch (_a) {
                        // ignore – non-fatal if the browser refuses capture
                    }
                }
                const end = () => {
                    toggleClass(this._element, 'dv-resize-container-dragging', false);
                    this._dragMove.value = exports.DockviewDisposable.NONE;
                    this._onDidChangeEnd.fire();
                };
                this._dragMove.value = new CompositeDisposable({
                    dispose: () => {
                        iframes.release();
                        if (captureTarget &&
                            typeof pointerId === 'number' &&
                            typeof captureTarget.releasePointerCapture ===
                                'function') {
                            try {
                                captureTarget.releasePointerCapture(pointerId);
                            }
                            catch (_a) {
                                // ignore – pointer may already be released
                            }
                        }
                    },
                }, addDisposableListener(window, 'pointermove', (e) => {
                    if (this._dragCancelled) {
                        return;
                    }
                    const containerRect = this.options.container.getBoundingClientRect();
                    const x = e.clientX - containerRect.left;
                    const y = e.clientY - containerRect.top;
                    toggleClass(this._element, 'dv-resize-container-dragging', true);
                    const overlayRect = this._element.getBoundingClientRect();
                    if (offset === null) {
                        offset = {
                            x: e.clientX - overlayRect.left,
                            y: e.clientY - overlayRect.top,
                        };
                    }
                    const xOffset = Math.max(0, this.getMinimumWidth(overlayRect.width));
                    const yOffset = Math.max(0, this.getMinimumHeight(overlayRect.height));
                    const top = clamp(y - offset.y, -yOffset, Math.max(0, containerRect.height - overlayRect.height + yOffset));
                    const bottom = clamp(offset.y -
                        y +
                        containerRect.height -
                        overlayRect.height, -yOffset, Math.max(0, containerRect.height - overlayRect.height + yOffset));
                    const left = clamp(x - offset.x, -xOffset, Math.max(0, containerRect.width - overlayRect.width + xOffset));
                    const right = clamp(offset.x - x + containerRect.width - overlayRect.width, -xOffset, Math.max(0, containerRect.width - overlayRect.width + xOffset));
                    const bounds = {};
                    // Anchor to top or to bottom depending on which one is closer
                    if (top <= bottom) {
                        bounds.top = top;
                    }
                    else {
                        bounds.bottom = bottom;
                    }
                    // Anchor to left or to right depending on which one is closer
                    if (left <= right) {
                        bounds.left = left;
                    }
                    else {
                        bounds.right = right;
                    }
                    this.setBounds(bounds);
                    if (!hasMoved) {
                        hasMoved = true;
                        this._onDidStartMoving.fire();
                    }
                }), addDisposableListener(window, 'pointerup', end), addDisposableListener(window, 'pointercancel', end));
            };
            this.addDisposables(addDisposableListener(dragTarget, 'pointerdown', (event) => {
                if (event.defaultPrevented) {
                    event.preventDefault();
                    return;
                }
                // if somebody has marked this event then treat as a defaultPrevented
                // without actually calling event.preventDefault()
                if (quasiDefaultPrevented(event)) {
                    return;
                }
                track(dragTarget, event.pointerId);
            }), addDisposableListener(this.options.content, 'pointerdown', (event) => {
                if (event.defaultPrevented) {
                    return;
                }
                // if somebody has marked this event then treat as a defaultPrevented
                // without actually calling event.preventDefault()
                if (quasiDefaultPrevented(event)) {
                    return;
                }
                if (event.shiftKey) {
                    track(this.options.content, event.pointerId);
                }
            }), addDisposableListener(this.options.content, 'pointerdown', () => {
                arialLevelTracker.push(this._element);
            }, true));
            if (options.inDragMode) {
                track();
            }
        }
        setupResize(direction) {
            const resizeHandleElement = document.createElement('div');
            resizeHandleElement.className = `dv-resize-handle-${direction}`;
            this._element.appendChild(resizeHandleElement);
            const move = new MutableDisposable();
            this.addDisposables(move, addDisposableListener(resizeHandleElement, 'pointerdown', (e) => {
                e.preventDefault();
                let startPosition = null;
                const iframes = disableIframePointEvents();
                const pointerId = e.pointerId;
                if (typeof resizeHandleElement.setPointerCapture === 'function') {
                    try {
                        resizeHandleElement.setPointerCapture(pointerId);
                    }
                    catch (_a) {
                        // ignore – non-fatal if the browser refuses capture
                    }
                }
                const end = () => {
                    move.dispose();
                    this._onDidChangeEnd.fire();
                };
                move.value = new CompositeDisposable(addDisposableListener(window, 'pointermove', (e) => {
                    const containerRect = this.options.container.getBoundingClientRect();
                    const overlayRect = this._element.getBoundingClientRect();
                    const y = e.clientY - containerRect.top;
                    const x = e.clientX - containerRect.left;
                    if (startPosition === null) {
                        // record the initial dimensions since as all subsequence moves are relative to this
                        startPosition = {
                            originalY: y,
                            originalHeight: overlayRect.height,
                            originalX: x,
                            originalWidth: overlayRect.width,
                        };
                    }
                    let top = undefined;
                    let bottom = undefined;
                    let height = undefined;
                    let left = undefined;
                    let right = undefined;
                    let width = undefined;
                    const moveTop = () => {
                        // When dragging top handle, constrain top position to prevent oversizing
                        const maxTop = startPosition.originalY +
                            startPosition.originalHeight >
                            containerRect.height
                            ? Math.max(0, containerRect.height -
                                Overlay.MINIMUM_HEIGHT)
                            : Math.max(0, startPosition.originalY +
                                startPosition.originalHeight -
                                Overlay.MINIMUM_HEIGHT);
                        top = clamp(y, 0, maxTop);
                        height =
                            startPosition.originalY +
                                startPosition.originalHeight -
                                top;
                        bottom = containerRect.height - top - height;
                    };
                    const moveBottom = () => {
                        top =
                            startPosition.originalY -
                                startPosition.originalHeight;
                        // When dragging bottom handle, constrain height to container height
                        const minHeight = top < 0 &&
                            typeof this.options.minimumInViewportHeight ===
                                'number'
                            ? -top +
                                this.options.minimumInViewportHeight
                            : Overlay.MINIMUM_HEIGHT;
                        const maxHeight = containerRect.height - Math.max(0, top);
                        height = clamp(y - top, minHeight, maxHeight);
                        bottom = containerRect.height - top - height;
                    };
                    const moveLeft = () => {
                        const maxLeft = startPosition.originalX +
                            startPosition.originalWidth >
                            containerRect.width
                            ? Math.max(0, containerRect.width -
                                Overlay.MINIMUM_WIDTH) // Prevent extending beyong right edge
                            : Math.max(0, startPosition.originalX +
                                startPosition.originalWidth -
                                Overlay.MINIMUM_WIDTH);
                        left = clamp(x, 0, maxLeft); // min is 0 (Not -Infinity) to prevent dragging beyond left edge
                        width =
                            startPosition.originalX +
                                startPosition.originalWidth -
                                left;
                        right = containerRect.width - left - width;
                    };
                    const moveRight = () => {
                        left =
                            startPosition.originalX -
                                startPosition.originalWidth;
                        // When dragging right handle, constrain width to container width
                        const minWidth = left < 0 &&
                            typeof this.options.minimumInViewportWidth ===
                                'number'
                            ? -left +
                                this.options.minimumInViewportWidth
                            : Overlay.MINIMUM_WIDTH;
                        const maxWidth = containerRect.width - Math.max(0, left);
                        width = clamp(x - left, minWidth, maxWidth);
                        right = containerRect.width - left - width;
                    };
                    switch (direction) {
                        case 'top':
                            moveTop();
                            break;
                        case 'bottom':
                            moveBottom();
                            break;
                        case 'left':
                            moveLeft();
                            break;
                        case 'right':
                            moveRight();
                            break;
                        case 'topleft':
                            moveTop();
                            moveLeft();
                            break;
                        case 'topright':
                            moveTop();
                            moveRight();
                            break;
                        case 'bottomleft':
                            moveBottom();
                            moveLeft();
                            break;
                        case 'bottomright':
                            moveBottom();
                            moveRight();
                            break;
                    }
                    const bounds = {};
                    // Anchor to top or to bottom depending on which one is closer
                    if (top <= bottom) {
                        bounds.top = top;
                    }
                    else {
                        bounds.bottom = bottom;
                    }
                    // Anchor to left or to right depending on which one is closer
                    if (left <= right) {
                        bounds.left = left;
                    }
                    else {
                        bounds.right = right;
                    }
                    bounds.height = height;
                    bounds.width = width;
                    this.setBounds(bounds);
                }), {
                    dispose: () => {
                        iframes.release();
                        if (typeof resizeHandleElement.releasePointerCapture ===
                            'function') {
                            try {
                                resizeHandleElement.releasePointerCapture(pointerId);
                            }
                            catch (_a) {
                                // ignore – pointer may already be released
                            }
                        }
                    },
                }, addDisposableListener(window, 'pointerup', end), addDisposableListener(window, 'pointercancel', end));
            }));
        }
        getMinimumWidth(width) {
            if (typeof this.options.minimumInViewportWidth === 'number') {
                return width - this.options.minimumInViewportWidth;
            }
            return 0;
        }
        getMinimumHeight(height) {
            if (typeof this.options.minimumInViewportHeight === 'number') {
                return height - this.options.minimumInViewportHeight;
            }
            return 0;
        }
        dispose() {
            arialLevelTracker.destroy(this._element);
            this._element.remove();
            super.dispose();
        }
    }
    Overlay.MINIMUM_HEIGHT = 20;
    Overlay.MINIMUM_WIDTH = 20;

    class DockviewFloatingGroupPanel extends CompositeDisposable {
        constructor(group, overlay) {
            super();
            this.group = group;
            this.overlay = overlay;
            this.addDisposables(overlay);
        }
        position(bounds) {
            this.overlay.setBounds(bounds);
        }
    }

    const DEFAULT_FLOATING_GROUP_OVERFLOW_SIZE = 100;
    const DEFAULT_FLOATING_GROUP_POSITION = {
        left: 100,
        top: 100,
        width: 300,
        height: 300,
    };
    const DESERIALIZATION_POPOUT_DELAY_MS = 100;

    class PositionCache {
        constructor() {
            this.cache = new Map();
            this.currentFrameId = 0;
            this.rafId = null;
        }
        getPosition(element) {
            const cached = this.cache.get(element);
            if (cached && cached.frameId === this.currentFrameId) {
                return cached.rect;
            }
            this.scheduleFrameUpdate();
            const rect = getDomNodePagePosition(element);
            this.cache.set(element, { rect, frameId: this.currentFrameId });
            return rect;
        }
        invalidate() {
            this.currentFrameId++;
        }
        scheduleFrameUpdate() {
            if (this.rafId)
                return;
            this.rafId = requestAnimationFrame(() => {
                this.currentFrameId++;
                this.rafId = null;
            });
        }
    }
    function createFocusableElement() {
        const element = document.createElement('div');
        element.tabIndex = -1;
        return element;
    }
    class OverlayRenderContainer extends CompositeDisposable {
        constructor(element, accessor) {
            super();
            this.element = element;
            this.accessor = accessor;
            this.map = {};
            this._disposed = false;
            this.positionCache = new PositionCache();
            this.pendingUpdates = new Set();
            this.addDisposables(exports.DockviewDisposable.from(() => {
                for (const value of Object.values(this.map)) {
                    value.disposable.dispose();
                    value.destroy.dispose();
                }
                this._disposed = true;
            }));
        }
        updateAllPositions() {
            if (this._disposed) {
                return;
            }
            // Invalidate position cache to force recalculation
            this.positionCache.invalidate();
            // Call resize function directly for all visible panels
            for (const entry of Object.values(this.map)) {
                if (entry.panel.api.isVisible && entry.resize) {
                    entry.resize();
                }
            }
        }
        detatch(panel) {
            if (this.map[panel.api.id]) {
                const { disposable, destroy } = this.map[panel.api.id];
                disposable.dispose();
                destroy.dispose();
                delete this.map[panel.api.id];
                return true;
            }
            return false;
        }
        attach(options) {
            const { panel, referenceContainer } = options;
            if (!this.map[panel.api.id]) {
                const element = createFocusableElement();
                element.className = 'dv-render-overlay';
                // Hide until the first RAF-based position is applied to prevent a
                // one-frame flash at position 0,0 when the element is first attached.
                element.style.visibility = 'hidden';
                this.map[panel.api.id] = {
                    panel,
                    disposable: exports.DockviewDisposable.NONE,
                    destroy: exports.DockviewDisposable.NONE,
                    element,
                };
            }
            const focusContainer = this.map[panel.api.id].element;
            // Capture the content element now so the destroy disposable below
            // does not re-query the renderer's `element` getter during teardown.
            // Some framework adapters (e.g. dockview-angular) tear down their
            // backing renderer before this disposable fires; reading through the
            // getter at that point can throw.
            const contentElement = panel.view.content.element;
            if (contentElement.parentElement !== focusContainer) {
                focusContainer.appendChild(contentElement);
            }
            if (focusContainer.parentElement !== this.element) {
                this.element.appendChild(focusContainer);
            }
            const resize = () => {
                const panelId = panel.api.id;
                if (this.pendingUpdates.has(panelId)) {
                    return; // Update already scheduled
                }
                this.pendingUpdates.add(panelId);
                requestAnimationFrame(() => {
                    this.pendingUpdates.delete(panelId);
                    if (this.isDisposed || !this.map[panelId]) {
                        return;
                    }
                    const box = this.positionCache.getPosition(referenceContainer.element);
                    const box2 = this.positionCache.getPosition(this.element);
                    // Use traditional positioning for overlay containers
                    const left = box.left - box2.left;
                    const top = box.top - box2.top;
                    const width = box.width;
                    const height = box.height;
                    focusContainer.style.left = `${left}px`;
                    focusContainer.style.top = `${top}px`;
                    focusContainer.style.width = `${width}px`;
                    focusContainer.style.height = `${height}px`;
                    // Sync visibility/pointer-events with the panel's current
                    // visibility at paint time. visibilityChanged() may have
                    // flipped to hidden between scheduling this rAF and now;
                    // unconditionally clearing `visibility:hidden` here would
                    // leave a hidden panel visually visible at a stale position,
                    // because onDidDimensionsChange skips non-visible panels and
                    // never recomputes their box on subsequent resizes.
                    if (panel.api.isVisible) {
                        focusContainer.style.visibility = '';
                        focusContainer.style.pointerEvents = '';
                    }
                    else {
                        focusContainer.style.visibility = 'hidden';
                        focusContainer.style.pointerEvents = 'none';
                    }
                    toggleClass(focusContainer, 'dv-render-overlay-float', panel.group.api.location.type === 'floating');
                });
            };
            const visibilityChanged = () => {
                if (panel.api.isVisible) {
                    this.positionCache.invalidate();
                    resize();
                    focusContainer.style.pointerEvents = '';
                }
                else {
                    focusContainer.style.visibility = 'hidden';
                    focusContainer.style.pointerEvents = 'none';
                }
            };
            const observerDisposable = new MutableDisposable();
            const correctLayerPosition = () => {
                if (panel.api.location.type === 'floating') {
                    queueMicrotask(() => {
                        const floatingGroup = this.accessor.floatingGroups.find((group) => group.group === panel.api.group);
                        if (!floatingGroup) {
                            return;
                        }
                        const element = floatingGroup.overlay.element;
                        const update = () => {
                            const level = Number(element.getAttribute('aria-level'));
                            focusContainer.style.zIndex = `calc(var(--dv-overlay-z-index, 999) + ${level * 2 + 1})`;
                        };
                        const observer = new MutationObserver(() => {
                            update();
                        });
                        observerDisposable.value = exports.DockviewDisposable.from(() => observer.disconnect());
                        observer.observe(element, {
                            attributeFilter: ['aria-level'],
                            attributes: true,
                        });
                        update();
                    });
                }
                else {
                    focusContainer.style.zIndex = ''; // reset the z-index, perhaps CSS will take over here
                }
            };
            const disposable = new CompositeDisposable(observerDisposable,
            /**
             * since container is positioned absoutely we must explicitly forward
             * the dnd events for the expect behaviours to continue to occur in terms of dnd
             *
             * the dnd observer does not need to be conditional on whether the panel is visible since
             * non-visible panels have 'pointer-events: none' and in such case the dnd observer will not fire.
             */
            new DragAndDropObserver(focusContainer, {
                onDragEnd: (e) => {
                    referenceContainer.dropTarget.dnd.onDragEnd(e);
                },
                onDragEnter: (e) => {
                    referenceContainer.dropTarget.dnd.onDragEnter(e);
                },
                onDragLeave: (e) => {
                    referenceContainer.dropTarget.dnd.onDragLeave(e);
                },
                onDrop: (e) => {
                    referenceContainer.dropTarget.dnd.onDrop(e);
                },
                onDragOver: (e) => {
                    referenceContainer.dropTarget.dnd.onDragOver(e);
                },
            }), panel.api.onDidVisibilityChange(() => {
                /**
                 * Control the visibility of the content, however even when not visible (display: none)
                 * the content is still maintained within the DOM hence DOM specific attributes
                 * such as scroll position are maintained when next made visible.
                 */
                visibilityChanged();
            }), panel.api.onDidDimensionsChange(() => {
                if (!panel.api.isVisible) {
                    return;
                }
                resize();
            }), panel.api.onDidLocationChange(() => {
                correctLayerPosition();
            }));
            this.map[panel.api.id].destroy = exports.DockviewDisposable.from(() => {
                var _a;
                if (contentElement.parentElement === focusContainer) {
                    focusContainer.removeChild(contentElement);
                }
                (_a = focusContainer.parentElement) === null || _a === void 0 ? void 0 : _a.removeChild(focusContainer);
            });
            correctLayerPosition();
            queueMicrotask(() => {
                if (this.isDisposed) {
                    return;
                }
                /**
                 * wait until everything has finished in the current stack-frame call before
                 * calling the first resize as other size-altering events may still occur before
                 * the end of the stack-frame.
                 */
                visibilityChanged();
            });
            // dispose of logic asoccciated with previous reference-container
            this.map[panel.api.id].disposable.dispose();
            // and reset the disposable to the active reference-container
            this.map[panel.api.id].disposable = disposable;
            // store the resize function for direct access
            this.map[panel.api.id].resize = resize;
            return focusContainer;
        }
    }

    /******************************************************************************
    Copyright (c) Microsoft Corporation.

    Permission to use, copy, modify, and/or distribute this software for any
    purpose with or without fee is hereby granted.

    THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
    REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
    AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
    INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
    LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR
    OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
    PERFORMANCE OF THIS SOFTWARE.
    ***************************************************************************** */
    /* global Reflect, Promise, SuppressedError, Symbol, Iterator */


    function __awaiter(thisArg, _arguments, P, generator) {
        function adopt(value) { return value instanceof P ? value : new P(function (resolve) { resolve(value); }); }
        return new (P || (P = Promise))(function (resolve, reject) {
            function fulfilled(value) { try { step(generator.next(value)); } catch (e) { reject(e); } }
            function rejected(value) { try { step(generator["throw"](value)); } catch (e) { reject(e); } }
            function step(result) { result.done ? resolve(result.value) : adopt(result.value).then(fulfilled, rejected); }
            step((generator = generator.apply(thisArg, _arguments || [])).next());
        });
    }

    typeof SuppressedError === "function" ? SuppressedError : function (error, suppressed, message) {
        var e = new Error(message);
        return e.name = "SuppressedError", e.error = error, e.suppressed = suppressed, e;
    };

    /**
     * Reject popout URLs that aren't same-origin http(s). Blocks `javascript:`,
     * `data:`, `blob:`, `vbscript:`, and cross-origin URLs that would otherwise
     * execute in a context the browser still associates with the opener via
     * `window.opener`.
     */
    function assertSameOriginPopoutUrl(url) {
        let resolved;
        try {
            resolved = new URL(url, window.location.href);
        }
        catch (_a) {
            throw new Error(`dockview: invalid popout URL: ${url}`);
        }
        const protocolOk = resolved.protocol === 'http:' || resolved.protocol === 'https:';
        if (!protocolOk || resolved.origin !== window.location.origin) {
            throw new Error(`dockview: popout URL must be same-origin http(s); got: ${url}`);
        }
    }
    class PopoutWindow extends CompositeDisposable {
        get window() {
            var _a, _b;
            return (_b = (_a = this._window) === null || _a === void 0 ? void 0 : _a.value) !== null && _b !== void 0 ? _b : null;
        }
        constructor(target, className, options) {
            super();
            this.target = target;
            this.className = className;
            this.options = options;
            this._onWillClose = new Emitter();
            this.onWillClose = this._onWillClose.event;
            this._onDidClose = new Emitter();
            this.onDidClose = this._onDidClose.event;
            this._window = null;
            this.addDisposables(this._onWillClose, this._onDidClose, {
                dispose: () => {
                    this.close();
                },
            });
        }
        dimensions() {
            if (!this._window) {
                return null;
            }
            const left = this._window.value.screenX;
            const top = this._window.value.screenY;
            const width = this._window.value.innerWidth;
            const height = this._window.value.innerHeight;
            return { top, left, width, height };
        }
        close() {
            var _a, _b;
            if (this._window) {
                this._onWillClose.fire();
                (_b = (_a = this.options).onWillClose) === null || _b === void 0 ? void 0 : _b.call(_a, {
                    id: this.target,
                    window: this._window.value,
                });
                this._window.disposable.dispose();
                this._window = null;
                this._onDidClose.fire();
            }
        }
        open() {
            return __awaiter(this, void 0, void 0, function* () {
                var _a, _b;
                if (this._window) {
                    throw new Error('instance of popout window is already open');
                }
                const url = `${this.options.url}`;
                assertSameOriginPopoutUrl(url);
                const features = Object.entries({
                    top: this.options.top,
                    left: this.options.left,
                    width: this.options.width,
                    height: this.options.height,
                })
                    .map(([key, value]) => `${key}=${value}`)
                    .join(',');
                /**
                 * @see https://developer.mozilla.org/en-US/docs/Web/API/Window/open
                 */
                const externalWindow = window.open(url, this.target, features);
                if (!externalWindow) {
                    /**
                     * Popup blocked
                     */
                    return null;
                }
                const disposable = new CompositeDisposable();
                this._window = { value: externalWindow, disposable };
                disposable.addDisposables(exports.DockviewDisposable.from(() => {
                    externalWindow.close();
                }), addDisposableListener(window, 'beforeunload', () => {
                    /**
                     * before the main window closes we should close this popup too
                     * to be good citizens
                     *
                     * @see https://developer.mozilla.org/en-US/docs/Web/API/Window/beforeunload_event
                     */
                    this.close();
                }));
                const container = this.createPopoutWindowContainer();
                if (this.className) {
                    container.classList.add(this.className);
                }
                (_b = (_a = this.options).onDidOpen) === null || _b === void 0 ? void 0 : _b.call(_a, {
                    id: this.target,
                    window: externalWindow,
                });
                return new Promise((resolve, reject) => {
                    externalWindow.addEventListener('unload', (e) => {
                        // if page fails to load before unloading
                        // this.close();
                    });
                    externalWindow.addEventListener('load', () => {
                        /**
                         * @see https://developer.mozilla.org/en-US/docs/Web/API/Window/load_event
                         */
                        try {
                            const externalDocument = externalWindow.document;
                            externalDocument.title = document.title;
                            externalDocument.body.appendChild(container);
                            addStyles(externalDocument, window.document.styleSheets, {
                                nonce: this.options.nonce,
                            });
                            /**
                             * beforeunload must be registered after load for reasons I could not determine
                             * otherwise the beforeunload event will not fire when the window is closed
                             */
                            addDisposableListener(externalWindow, 'beforeunload', () => {
                                /**
                                 * @see https://developer.mozilla.org/en-US/docs/Web/API/Window/beforeunload_event
                                 */
                                this.close();
                            });
                            resolve(container);
                        }
                        catch (err) {
                            // only except this is the DOM isn't setup. e.g. in a in correctly configured test
                            reject(err);
                        }
                    });
                });
            });
        }
        createPopoutWindowContainer() {
            const el = document.createElement('div');
            el.classList.add('dv-popout-window');
            el.id = 'dv-popout-window';
            el.style.position = 'absolute';
            el.style.width = '100%';
            el.style.height = '100%';
            el.style.top = '0px';
            el.style.left = '0px';
            return el;
        }
    }

    class StrictEventsSequencing extends CompositeDisposable {
        constructor(accessor) {
            super();
            this.accessor = accessor;
            this.init();
        }
        init() {
            const panels = new Set();
            const groups = new Set();
            this.addDisposables(this.accessor.onDidAddPanel((panel) => {
                if (panels.has(panel.api.id)) {
                    throw new Error(`dockview: Invalid event sequence. [onDidAddPanel] called for panel ${panel.api.id} but panel already exists`);
                }
                else {
                    panels.add(panel.api.id);
                }
            }), this.accessor.onDidRemovePanel((panel) => {
                if (!panels.has(panel.api.id)) {
                    throw new Error(`dockview: Invalid event sequence. [onDidRemovePanel] called for panel ${panel.api.id} but panel does not exists`);
                }
                else {
                    panels.delete(panel.api.id);
                }
            }), this.accessor.onDidAddGroup((group) => {
                if (groups.has(group.api.id)) {
                    throw new Error(`dockview: Invalid event sequence. [onDidAddGroup] called for group ${group.api.id} but group already exists`);
                }
                else {
                    groups.add(group.api.id);
                }
            }), this.accessor.onDidRemoveGroup((group) => {
                if (!groups.has(group.api.id)) {
                    throw new Error(`dockview: Invalid event sequence. [onDidRemoveGroup] called for group ${group.api.id} but group does not exists`);
                }
                else {
                    groups.delete(group.api.id);
                }
            }));
        }
    }

    function isCoarsePrimaryInput$1(win) {
        if (!win.matchMedia) {
            return false;
        }
        const coarse = win.matchMedia('(pointer: coarse)').matches;
        const fine = win.matchMedia('(pointer: fine)').matches;
        return coarse && !fine;
    }
    class PopupService extends CompositeDisposable {
        constructor(root, win = window) {
            super();
            this._active = null;
            this._activeDisposable = new MutableDisposable();
            this._root = root;
            this._window = win;
            this._element = win.document.createElement('div');
            this._element.className = 'dv-popover-anchor';
            this._element.style.position = 'relative';
            this._root.prepend(this._element);
            this.addDisposables(exports.DockviewDisposable.from(() => {
                this.close();
            }), this._activeDisposable);
        }
        /**
         * Move the popup anchor into a new root element. Call this when a shell
         * wraps the dockview component so that edge-group overflow dropdowns
         * position correctly relative to the full layout area.
         */
        updateRoot(newRoot) {
            newRoot.prepend(this._element);
            this._root = newRoot;
        }
        openPopover(element, position) {
            var _a;
            this.close();
            const wrapper = this._window.document.createElement('div');
            wrapper.style.position = 'absolute';
            wrapper.style.zIndex = (_a = position.zIndex) !== null && _a !== void 0 ? _a : 'var(--dv-overlay-z-index)';
            wrapper.appendChild(element);
            const anchorBox = this._element.getBoundingClientRect();
            const offsetX = anchorBox.left;
            const offsetY = anchorBox.top;
            wrapper.style.top = `${position.y - offsetY}px`;
            wrapper.style.left = `${position.x - offsetX}px`;
            this._element.appendChild(wrapper);
            this._active = wrapper;
            // Outside-pointerdown dismissal is suppressed for a short grace
            // window after opening. Touch long-press callers (chip / tab context
            // menus) open the popover while the user's finger is still pressing
            // the source element — Android Chrome can dispatch a follow-up
            // synthetic pointerdown tied to the gesture, and the release-then-
            // retap motion can land just outside the wrapper. Either would
            // dismiss the popover before the user can see or interact with it.
            // The grace window is short enough that intentional outside taps
            // still feel responsive.
            const openedAt = Date.now();
            const POINTERDOWN_GRACE_MS = 200;
            this._activeDisposable.value = new CompositeDisposable(addDisposableListener(this._window, 'pointerdown', (event) => {
                var _a;
                if (Date.now() - openedAt < POINTERDOWN_GRACE_MS) {
                    return;
                }
                const target = event.target;
                if (!(target instanceof HTMLElement)) {
                    return;
                }
                let el = target;
                while (el && el !== wrapper) {
                    el = (_a = el === null || el === void 0 ? void 0 : el.parentElement) !== null && _a !== void 0 ? _a : null;
                }
                if (el) {
                    return; // clicked within popover
                }
                this.close();
            }), addDisposableListener(this._window, 'keydown', (event) => {
                if (event.key === 'Escape' || event.key === 'Enter') {
                    this.close();
                }
            }), addDisposableListener(this._window, 'resize', () => {
                // On touch-primary devices, common interactions resize the
                // window: on-screen keyboard pop, orientation change, browser
                // address-bar collapse. None of these mean "the user wants
                // the popover dismissed". Specifically, focusing the chip
                // context menu's rename input pops the keyboard, which would
                // otherwise close the menu the moment the user goes to edit
                // it. Desktop / hybrid input keeps the existing behaviour —
                // there a resize genuinely means the user has resized the
                // window and the popover position is now stale.
                if (isCoarsePrimaryInput$1(this._window)) {
                    return;
                }
                this.close();
            }));
            this._window.requestAnimationFrame(() => {
                shiftAbsoluteElementIntoView(wrapper, this._root);
            });
        }
        close() {
            if (this._active) {
                this._active.remove();
                this._activeDisposable.dispose();
                this._active = null;
            }
        }
    }

    function popoverZIndexFor(target) {
        if (!(target instanceof HTMLElement)) {
            return undefined;
        }
        // Floating overlays live in the shell as siblings of the popover anchor
        // and the AriaLevelTracker sets their inline z-index. Without this, a
        // popover opened from inside a floating group would render behind it
        // because they share the shell stacking context.
        const relativeParent = findRelativeZIndexParent(target);
        return (relativeParent === null || relativeParent === void 0 ? void 0 : relativeParent.style.zIndex)
            ? `calc(${relativeParent.style.zIndex} * 2)`
            : undefined;
    }
    let _nextId = 0;
    const nextContextMenuItemId = () => `dv-ctx-menu-item-${_nextId++}`;
    function isItemConfig(item) {
        return typeof item === 'object';
    }
    function buildItem(label, close, action, disabled) {
        const el = document.createElement('div');
        el.className = 'dv-context-menu-item';
        el.setAttribute('role', 'menuitem');
        if (disabled) {
            el.classList.add('dv-context-menu-item--disabled');
            el.setAttribute('aria-disabled', 'true');
        }
        el.textContent = label;
        if (!disabled) {
            el.addEventListener('click', () => {
                action();
                close();
            });
        }
        return el;
    }
    function buildSeparator() {
        const el = document.createElement('div');
        el.className = 'dv-context-menu-separator';
        el.setAttribute('role', 'separator');
        return el;
    }
    function isCoarsePrimaryInput() {
        if (typeof window === 'undefined' || !window.matchMedia) {
            return false;
        }
        const coarse = window.matchMedia('(pointer: coarse)').matches;
        const fine = window.matchMedia('(pointer: fine)').matches;
        return coarse && !fine;
    }
    function buildRenameInput(tabGroup) {
        const wrapper = document.createElement('div');
        wrapper.className = 'dv-context-menu-rename';
        const input = document.createElement('input');
        input.className = 'dv-context-menu-rename-input';
        input.type = 'text';
        input.placeholder = 'Name This Group';
        input.value = tabGroup.label;
        input.addEventListener('input', () => {
            tabGroup.setLabel(input.value);
        });
        input.addEventListener('keydown', (e) => {
            if (e.key !== 'Escape' && e.key !== 'Enter') {
                e.stopPropagation();
            }
        });
        input.addEventListener('click', (e) => {
            e.stopPropagation();
        });
        wrapper.appendChild(input);
        // Skip auto-focus on touch-primary devices: focusing the input pops the
        // on-screen keyboard, which fires `window resize`, which `PopupService`
        // listens to and uses to dismiss the popover — so the menu opens, the
        // keyboard appears, and the menu immediately closes before the user can
        // type. The user can still tap the input to focus it intentionally.
        if (!isCoarsePrimaryInput()) {
            requestAnimationFrame(() => {
                input.focus();
                input.select();
            });
        }
        return wrapper;
    }
    function buildColorPicker(tabGroup, palette) {
        const wrapper = document.createElement('div');
        wrapper.className = 'dv-context-menu-color-picker';
        if (!palette.enabled) {
            // Opt-out: render no swatches. Returning a wrapper rather than null
            // keeps the call site simple; the wrapper is empty and visually inert.
            return wrapper;
        }
        for (const entry of palette.entries()) {
            const swatch = document.createElement('div');
            swatch.className = 'dv-context-menu-color-swatch';
            // Use a CSS custom property rather than setting `backgroundColor`
            // directly: the IDL setter validates the value against a color
            // grammar and rejects `var(...)` references in some environments
            // (notably jsdom; some browsers have historically had similar
            // quirks). The matching SCSS rule reads the var at use time.
            swatch.style.setProperty('--dv-tab-group-color', entry.value);
            if (entry.label) {
                swatch.title = entry.label;
            }
            if (tabGroup.color === entry.id) {
                swatch.classList.add('dv-context-menu-color-swatch--selected');
            }
            swatch.addEventListener('click', () => {
                tabGroup.setColor(entry.id);
            });
            wrapper.appendChild(swatch);
        }
        return wrapper;
    }
    class ContextMenuController {
        constructor(accessor) {
            this.accessor = accessor;
        }
        show(panel, group, event) {
            var _a, _b;
            if (!this.accessor.options.getTabContextMenuItems) {
                return;
            }
            const items = this.accessor.options.getTabContextMenuItems({
                panel,
                group,
                api: this.accessor.api,
                event,
            });
            if (items.length === 0) {
                return;
            }
            event.preventDefault();
            const popupService = this.accessor.getPopupServiceForGroup(group);
            const close = () => popupService.close();
            const menuEl = document.createElement('div');
            menuEl.className = 'dv-context-menu';
            menuEl.setAttribute('role', 'menu');
            for (const item of items) {
                if (item === 'separator') {
                    menuEl.appendChild(buildSeparator());
                }
                else if (item === 'close') {
                    menuEl.appendChild(buildItem('Close', close, () => panel.api.close()));
                }
                else if (item === 'closeOthers') {
                    menuEl.appendChild(buildItem('Close Others', close, () => {
                        group.panels
                            .filter((p) => p !== panel)
                            .forEach((p) => p.api.close());
                    }));
                }
                else if (item === 'closeAll') {
                    menuEl.appendChild(buildItem('Close All', close, () => {
                        [...group.panels].forEach((p) => p.api.close());
                    }));
                }
                else if (isItemConfig(item) && item.element) {
                    menuEl.appendChild(item.element);
                }
                else if (isItemConfig(item) && item.component) {
                    const renderer = (_b = (_a = this.accessor.options).createContextMenuItemComponent) === null || _b === void 0 ? void 0 : _b.call(_a, {
                        id: nextContextMenuItemId(),
                        component: item.component,
                    });
                    if (renderer) {
                        renderer.init({
                            panel,
                            group,
                            api: this.accessor.api,
                            close,
                            componentProps: item.componentProps,
                        });
                        menuEl.appendChild(renderer.element);
                    }
                }
                else if (isItemConfig(item) && item.label) {
                    menuEl.appendChild(buildItem(item.label, close, () => { var _a; return (_a = item.action) === null || _a === void 0 ? void 0 : _a.call(item); }, item.disabled));
                }
            }
            popupService.openPopover(menuEl, {
                x: event.clientX,
                y: event.clientY,
                zIndex: popoverZIndexFor(event.target),
            });
        }
        showForChip(tabGroup, group, event) {
            if (!this.accessor.options.getTabGroupChipContextMenuItems) {
                return;
            }
            const items = this.accessor.options.getTabGroupChipContextMenuItems({
                tabGroup,
                group,
                api: this.accessor.api,
                event,
            });
            if (items.length === 0) {
                return;
            }
            event.preventDefault();
            const popupService = this.accessor.getPopupServiceForGroup(group);
            const close = () => popupService.close();
            const menuEl = document.createElement('div');
            menuEl.className = 'dv-context-menu';
            menuEl.setAttribute('role', 'menu');
            for (const item of items) {
                if (item === 'separator') {
                    menuEl.appendChild(buildSeparator());
                }
                else if (item === 'rename') {
                    menuEl.appendChild(buildRenameInput(tabGroup));
                }
                else if (item === 'colorPicker') {
                    menuEl.appendChild(buildColorPicker(tabGroup, this.accessor.tabGroupColorPalette));
                }
                else if (isItemConfig(item) && item.element) {
                    menuEl.appendChild(item.element);
                }
                else if (isItemConfig(item) && item.label) {
                    menuEl.appendChild(buildItem(item.label, close, () => { var _a; return (_a = item.action) === null || _a === void 0 ? void 0 : _a.call(item); }, item.disabled));
                }
            }
            popupService.openPopover(menuEl, {
                x: event.clientX,
                y: event.clientY,
                zIndex: popoverZIndexFor(event.target),
            });
        }
    }

    class DropTargetAnchorContainer extends CompositeDisposable {
        get disabled() {
            return this._disabled;
        }
        set disabled(value) {
            var _a;
            if (this.disabled === value) {
                return;
            }
            this._disabled = value;
            if (value) {
                (_a = this.model) === null || _a === void 0 ? void 0 : _a.clear();
            }
        }
        get model() {
            if (this.disabled) {
                return undefined;
            }
            return {
                clear: () => {
                    var _a;
                    if (this._model) {
                        (_a = this._model.root.parentElement) === null || _a === void 0 ? void 0 : _a.removeChild(this._model.root);
                    }
                    this._model = undefined;
                },
                exists: () => {
                    return !!this._model;
                },
                getElements: (event, outline) => {
                    const changed = this._outline !== outline;
                    this._outline = outline;
                    if (this._model) {
                        this._model.changed = changed;
                        return this._model;
                    }
                    const container = this.createContainer();
                    const anchor = this.createAnchor();
                    this._model = { root: container, overlay: anchor, changed };
                    container.appendChild(anchor);
                    this.element.appendChild(container);
                    if ((event === null || event === void 0 ? void 0 : event.target) instanceof HTMLElement) {
                        const targetBox = event.target.getBoundingClientRect();
                        const box = this.element.getBoundingClientRect();
                        anchor.style.left = `${targetBox.left - box.left}px`;
                        anchor.style.top = `${targetBox.top - box.top}px`;
                    }
                    return this._model;
                },
            };
        }
        constructor(element, options) {
            super();
            this.element = element;
            this._disabled = false;
            this._disabled = options.disabled;
            this.addDisposables(exports.DockviewDisposable.from(() => {
                var _a;
                (_a = this.model) === null || _a === void 0 ? void 0 : _a.clear();
            }));
        }
        createContainer() {
            const el = document.createElement('div');
            el.className = 'dv-drop-target-container';
            return el;
        }
        createAnchor() {
            const el = document.createElement('div');
            el.className = 'dv-drop-target-anchor';
            el.style.visibility = 'hidden';
            return el;
        }
    }

    class EdgeGroupView {
        get minimumSize() {
            // When collapsed, lock size to collapsedSize so sash can't drag it open
            return this._isCollapsed
                ? this._collapsedSize
                : this._expandedMinimumSize;
        }
        get maximumSize() {
            // When collapsed, lock size to collapsedSize so sash can't drag it open
            return this._isCollapsed
                ? this._collapsedSize
                : this._expandedMaximumSize;
        }
        get element() {
            return this._group.element;
        }
        get isCollapsed() {
            return this._isCollapsed;
        }
        get lastExpandedSize() {
            return this._lastExpandedSize;
        }
        get collapsedSize() {
            return this._collapsedSize;
        }
        constructor(options, group, orientation) {
            var _a, _b, _c;
            this._onDidChange = new Emitter();
            this.onDidChange = this._onDidChange.event;
            this.snap = false;
            this.priority = exports.LayoutPriority.Low;
            this._isCollapsed = false;
            this._group = group;
            this._orientation = orientation;
            group.element.classList.add('dv-edge-group');
            group.element.dataset.testid = `dv-edge-group-${options.id}`;
            this._collapsedSize = (_a = options.collapsedSize) !== null && _a !== void 0 ? _a : 35;
            this._expandedMaximumSize =
                (_b = options.maximumSize) !== null && _b !== void 0 ? _b : Number.POSITIVE_INFINITY;
            // If the caller explicitly provides a minimumSize, respect it.
            // Otherwise fall back to collapsedSize + 50 so the expanded state is
            // visually distinguishable from the collapsed state.
            this._expandedMinimumSize =
                options.minimumSize !== undefined
                    ? options.minimumSize
                    : this._collapsedSize + 50;
            this._lastExpandedSize = (_c = options.initialSize) !== null && _c !== void 0 ? _c : 200;
            if (options.collapsed) {
                this._isCollapsed = true;
                group.element.classList.add('dv-edge-collapsed');
            }
        }
        layout(size, orthogonalSize) {
            // Track the last expanded size so we can restore it after collapsing
            if (!this._isCollapsed) {
                this._lastExpandedSize = size;
            }
            // horizontal (left/right): size=width, orthogonalSize=height → layout(width, height)
            // vertical (top/bottom): size=height, orthogonalSize=width → layout(width, height)
            if (this._orientation === 'horizontal') {
                this._group.layout(size, orthogonalSize);
            }
            else {
                this._group.layout(orthogonalSize, size);
            }
        }
        setCollapsed(collapsed) {
            if (this._isCollapsed === collapsed) {
                return;
            }
            this._isCollapsed = collapsed;
            this._group.element.classList.toggle('dv-edge-collapsed', collapsed);
            // ShellManager calls resizeView directly after this; no _onDidChange needed
        }
        setVisible(_visible) {
            // visibility is managed by the parent splitview
        }
        /**
         * Restore the last-expanded size from serialized state without triggering
         * a layout. Must be called before setCollapsed(true) during fromJSON so
         * that expanding after deserialization restores the correct size.
         */
        restoreExpandedSize(size) {
            this._lastExpandedSize = size;
        }
        /**
         * Apply new effective collapsed and expanded-minimum sizes after a theme
         * or gap change. The caller (ShellManager) is responsible for computing
         * the correct values from the original config and the new gap.
         */
        updateCollapsedSize(newCollapsedSize, newExpandedMinimumSize) {
            this._collapsedSize = newCollapsedSize;
            this._expandedMinimumSize = newExpandedMinimumSize;
        }
        dispose() {
            this._onDidChange.dispose();
        }
    }
    class CenterView {
        get element() {
            return this._dockviewElement;
        }
        constructor(_dockviewElement, _layoutDockview) {
            this._dockviewElement = _dockviewElement;
            this._layoutDockview = _layoutDockview;
            this.priority = exports.LayoutPriority.High;
            this.minimumSize = 100;
            this.maximumSize = Number.POSITIVE_INFINITY;
            this._onDidChange = new Emitter();
            this.onDidChange = this._onDidChange.event;
        }
        layout(size, orthogonalSize) {
            // Lives in a VERTICAL middle-column splitview:
            // size = height alloc, orthogonalSize = width
            this._layoutDockview(orthogonalSize, size);
        }
        setVisible(_visible) {
            // center is always visible
        }
        dispose() {
            this._onDidChange.dispose();
        }
    }
    /**
     * The vertical centre column: top (optional) | center | bottom (optional).
     * This view sits between the left and right edge panels in the outer
     * horizontal splitview, so its primary axis is width (horizontal).
     */
    class MiddleColumnView {
        get element() {
            return this._element;
        }
        constructor(centerView, gap = 0) {
            this._onDidChange = new Emitter();
            this.onDidChange = this._onDidChange.event;
            this.minimumSize = 100;
            this.maximumSize = Number.POSITIVE_INFINITY;
            this.priority = exports.LayoutPriority.High;
            this._element = document.createElement('div');
            this._element.className = 'dv-shell-middle-column';
            this._element.style.height = '100%';
            this._element.style.width = '100%';
            this._splitview = new Splitview(this._element, {
                orientation: exports.Orientation.VERTICAL,
                proportionalLayout: false,
                margin: gap,
            });
            this._centerIndex = 0;
            this._splitview.addView(centerView, { type: 'distribute' }, 0);
        }
        addTopView(view, initialSize) {
            // Insert before center
            this._splitview.addView(view, initialSize, 0);
            this._topIndex = 0;
            this._centerIndex += 1;
            if (this._bottomIndex !== undefined) {
                this._bottomIndex += 1;
            }
        }
        addBottomView(view, initialSize) {
            // Append after center (and any existing bottom — shouldn't happen but safe)
            const newIndex = this._splitview.length;
            this._splitview.addView(view, initialSize, newIndex);
            this._bottomIndex = newIndex;
        }
        removeView(position) {
            const index = position === 'top' ? this._topIndex : this._bottomIndex;
            if (index === undefined) {
                return;
            }
            this._splitview.removeView(index);
            if (position === 'top') {
                this._topIndex = undefined;
                // center (and bottom if present) shift down by one
                this._centerIndex -= 1;
                if (this._bottomIndex !== undefined) {
                    this._bottomIndex -= 1;
                }
            }
            else {
                this._bottomIndex = undefined;
                // center and top are unaffected
            }
        }
        layout(size, orthogonalSize) {
            // Outer horizontal splitview: size = width, orthogonalSize = height
            // Inner vertical splitview: layout(height, width)
            this._splitview.layout(orthogonalSize, size);
        }
        setVisible(_visible) {
            // middle column is always visible
        }
        setViewVisible(position, visible) {
            const index = position === 'top' ? this._topIndex : this._bottomIndex;
            if (index !== undefined) {
                this._splitview.setViewVisible(index, visible);
            }
        }
        isViewVisible(position) {
            const index = position === 'top' ? this._topIndex : this._bottomIndex;
            if (index !== undefined) {
                return this._splitview.isViewVisible(index);
            }
            return false;
        }
        getViewSize(position) {
            const index = position === 'top' ? this._topIndex : this._bottomIndex;
            if (index !== undefined) {
                return this._splitview.getViewSize(index);
            }
            return 0;
        }
        resizeView(position, size) {
            const index = position === 'top' ? this._topIndex : this._bottomIndex;
            if (index !== undefined) {
                this._splitview.resizeView(index, size);
            }
        }
        updateMargin(gap) {
            this._splitview.margin = gap;
        }
        dispose() {
            this._onDidChange.dispose();
            this._splitview.dispose();
        }
    }
    function adjustedOpts(base, defaultCollapsed, gapAdd) {
        var _a;
        const effectiveCollapsed = ((_a = base.collapsedSize) !== null && _a !== void 0 ? _a : defaultCollapsed) + gapAdd;
        const result = Object.assign(Object.assign({}, base), { collapsedSize: effectiveCollapsed });
        if (base.minimumSize !== undefined) {
            result.minimumSize = base.minimumSize + gapAdd;
        }
        return result;
    }
    class ShellManager {
        constructor(container, dockviewElement, layoutGrid, gap = 0, defaultCollapsedSize = 35) {
            this._disposables = new CompositeDisposable();
            // Retained for updateTheme() recalculations.
            this._viewConfigs = new Map();
            this._currentWidth = 0;
            this._currentHeight = 0;
            this._gap = gap;
            this._defaultCollapsedSize = defaultCollapsedSize;
            this._shellElement = document.createElement('div');
            this._shellElement.className = 'dv-shell';
            this._shellElement.style.height = '100%';
            this._shellElement.style.width = '100%';
            this._shellElement.style.position = 'relative';
            container.appendChild(this._shellElement);
            const centerView = new CenterView(dockviewElement, layoutGrid);
            this._middleColumn = new MiddleColumnView(centerView, gap);
            this._outerSplitview = new Splitview(this._shellElement, {
                orientation: exports.Orientation.HORIZONTAL,
                proportionalLayout: false,
                margin: gap,
            });
            this._middleIndex = 0;
            this._outerSplitview.addView(this._middleColumn, { type: 'distribute' }, 0);
            this._disposables.addDisposables(watchElementResize(this._shellElement, (entry) => {
                const width = Math.round(entry.contentRect.width);
                const height = Math.round(entry.contentRect.height);
                if (width === this._currentWidth &&
                    height === this._currentHeight) {
                    return;
                }
                this._currentWidth = width;
                this._currentHeight = height;
                this.layout(width, height);
            }), this._outerSplitview, this._middleColumn, centerView);
        }
        get element() {
            return this._shellElement;
        }
        /**
         * Add an edge group view at the given position. The view wraps the
         * provided group element inside the shell's splitview layout.
         * Throws if a group at this position is already registered.
         */
        addEdgeView(position, options, group) {
            if (this.hasEdgeGroup(position)) {
                throw new Error(`dockview: edge group already registered at position '${position}'`);
            }
            this._viewConfigs.set(position, options);
            // Recompute gap adjustments now that _viewConfigs has grown.
            const outerN = 1 +
                (this._viewConfigs.has('left') ? 1 : 0) +
                (this._viewConfigs.has('right') ? 1 : 0);
            const innerN = 1 +
                (this._viewConfigs.has('top') ? 1 : 0) +
                (this._viewConfigs.has('bottom') ? 1 : 0);
            const outerGapAdd = outerN > 1 ? (this._gap * (outerN - 1)) / outerN : 0;
            const innerGapAdd = innerN > 1 ? (this._gap * (innerN - 1)) / innerN : 0;
            const isHorizontal = position === 'left' || position === 'right';
            const gapAdd = isHorizontal ? outerGapAdd : innerGapAdd;
            const orientation = isHorizontal ? 'horizontal' : 'vertical';
            const view = new EdgeGroupView(adjustedOpts(Object.assign({ collapsedSize: this._defaultCollapsedSize }, options), this._defaultCollapsedSize, gapAdd), group, orientation);
            const initialSize = view.isCollapsed
                ? view.collapsedSize
                : view.lastExpandedSize;
            switch (position) {
                case 'left':
                    // Insert before the middle column
                    this._outerSplitview.addView(view, initialSize, 0);
                    this._leftIndex = 0;
                    this._middleIndex += 1;
                    if (this._rightIndex !== undefined) {
                        this._rightIndex += 1;
                    }
                    this._leftView = view;
                    break;
                case 'right':
                    // Append after the middle column
                    {
                        const idx = this._outerSplitview.length;
                        this._outerSplitview.addView(view, initialSize, idx);
                        this._rightIndex = idx;
                        this._rightView = view;
                    }
                    break;
                case 'top':
                    this._middleColumn.addTopView(view, initialSize);
                    this._topView = view;
                    break;
                case 'bottom':
                    this._middleColumn.addBottomView(view, initialSize);
                    this._bottomView = view;
                    break;
            }
            this._disposables.addDisposables(view);
            // Recalculate gap adjustments for all views now that n has changed.
            // updateTheme already guards the layout() call by _currentWidth/_currentHeight.
            this.updateTheme(this._gap, this._defaultCollapsedSize);
            return view;
        }
        layout(width, height) {
            // Outer splitview is HORIZONTAL: layout(size=width, orthogonalSize=height)
            this._outerSplitview.layout(width, height);
        }
        /**
         * Called when the active theme changes. Updates splitview margins and
         * edge-group collapsed sizes so the layout matches the new theme's gap
         * and tab-strip dimensions.
         */
        updateTheme(gap, defaultCollapsedSize) {
            var _a, _b, _c, _d;
            this._gap = gap;
            this._defaultCollapsedSize = defaultCollapsedSize;
            const outerN = 1 +
                (this._viewConfigs.has('left') ? 1 : 0) +
                (this._viewConfigs.has('right') ? 1 : 0);
            const innerN = 1 +
                (this._viewConfigs.has('top') ? 1 : 0) +
                (this._viewConfigs.has('bottom') ? 1 : 0);
            const outerGapAdd = outerN > 1 ? (gap * (outerN - 1)) / outerN : 0;
            const innerGapAdd = innerN > 1 ? (gap * (innerN - 1)) / innerN : 0;
            // Update splitview margins.
            this._outerSplitview.margin = gap;
            this._middleColumn.updateMargin(gap);
            // Recompute effective collapsed sizes from the original config values.
            const updateView = (view, baseCfg, gapAdd) => {
                var _a;
                const baseCS = (_a = baseCfg.collapsedSize) !== null && _a !== void 0 ? _a : defaultCollapsedSize;
                const newCS = baseCS + gapAdd;
                const baseMS = baseCfg.minimumSize;
                const newMS = baseMS !== undefined ? baseMS + gapAdd : newCS + 50;
                view.updateCollapsedSize(newCS, newMS);
            };
            const topCfg = this._viewConfigs.get('top');
            if (this._topView && topCfg) {
                updateView(this._topView, topCfg, innerGapAdd);
            }
            const bottomCfg = this._viewConfigs.get('bottom');
            if (this._bottomView && bottomCfg) {
                updateView(this._bottomView, bottomCfg, innerGapAdd);
            }
            const leftCfg = this._viewConfigs.get('left');
            if (this._leftView && leftCfg) {
                updateView(this._leftView, leftCfg, outerGapAdd);
            }
            const rightCfg = this._viewConfigs.get('right');
            if (this._rightView && rightCfg) {
                updateView(this._rightView, rightCfg, outerGapAdd);
            }
            // Resize currently-collapsed groups to their new collapsed size so
            // they immediately match the new theme's tab-strip dimensions.
            if (((_a = this._leftView) === null || _a === void 0 ? void 0 : _a.isCollapsed) && this._leftIndex !== undefined) {
                this._outerSplitview.resizeView(this._leftIndex, this._leftView.collapsedSize);
            }
            if (((_b = this._rightView) === null || _b === void 0 ? void 0 : _b.isCollapsed) && this._rightIndex !== undefined) {
                this._outerSplitview.resizeView(this._rightIndex, this._rightView.collapsedSize);
            }
            if ((_c = this._topView) === null || _c === void 0 ? void 0 : _c.isCollapsed) {
                this._middleColumn.resizeView('top', this._topView.collapsedSize);
            }
            if ((_d = this._bottomView) === null || _d === void 0 ? void 0 : _d.isCollapsed) {
                this._middleColumn.resizeView('bottom', this._bottomView.collapsedSize);
            }
            // Re-run layout with the current shell dimensions.
            if (this._currentWidth > 0 && this._currentHeight > 0) {
                this.layout(this._currentWidth, this._currentHeight);
            }
        }
        removeEdgeView(position) {
            const view = this._getView(position);
            if (!view) {
                return;
            }
            switch (position) {
                case 'left':
                    this._outerSplitview.removeView(this._leftIndex);
                    this._leftIndex = undefined;
                    this._leftView = undefined;
                    // middle and right shift left by one
                    this._middleIndex -= 1;
                    if (this._rightIndex !== undefined) {
                        this._rightIndex -= 1;
                    }
                    break;
                case 'right':
                    this._outerSplitview.removeView(this._rightIndex);
                    this._rightIndex = undefined;
                    this._rightView = undefined;
                    break;
                case 'top':
                    this._middleColumn.removeView('top');
                    this._topView = undefined;
                    break;
                case 'bottom':
                    this._middleColumn.removeView('bottom');
                    this._bottomView = undefined;
                    break;
            }
            // Deregister before disposing to avoid double-dispose when ShellManager
            // itself is eventually disposed.
            this._disposables.removeDisposable(view);
            view.dispose();
            this._viewConfigs.delete(position);
            // Recalculate gap adjustments for remaining views.
            this.updateTheme(this._gap, this._defaultCollapsedSize);
        }
        hasEdgeGroup(position) {
            switch (position) {
                case 'top':
                    return this._topView !== undefined;
                case 'bottom':
                    return this._bottomView !== undefined;
                case 'left':
                    return this._leftView !== undefined;
                case 'right':
                    return this._rightView !== undefined;
            }
        }
        setEdgeGroupVisible(position, visible) {
            switch (position) {
                case 'left':
                    if (this._leftIndex !== undefined) {
                        this._outerSplitview.setViewVisible(this._leftIndex, visible);
                    }
                    break;
                case 'right':
                    if (this._rightIndex !== undefined) {
                        this._outerSplitview.setViewVisible(this._rightIndex, visible);
                    }
                    break;
                case 'top':
                case 'bottom':
                    this._middleColumn.setViewVisible(position, visible);
                    break;
            }
        }
        isEdgeGroupVisible(position) {
            switch (position) {
                case 'left':
                    if (this._leftIndex !== undefined) {
                        return this._outerSplitview.isViewVisible(this._leftIndex);
                    }
                    return false;
                case 'right':
                    if (this._rightIndex !== undefined) {
                        return this._outerSplitview.isViewVisible(this._rightIndex);
                    }
                    return false;
                case 'top':
                case 'bottom':
                    return this._middleColumn.isViewVisible(position);
            }
        }
        setEdgeGroupCollapsed(position, collapsed) {
            const view = this._getView(position);
            if (!view) {
                return;
            }
            view.setCollapsed(collapsed);
            const targetSize = collapsed
                ? view.collapsedSize
                : view.lastExpandedSize;
            switch (position) {
                case 'left':
                    if (this._leftIndex !== undefined) {
                        this._outerSplitview.resizeView(this._leftIndex, targetSize);
                    }
                    break;
                case 'right':
                    if (this._rightIndex !== undefined) {
                        this._outerSplitview.resizeView(this._rightIndex, targetSize);
                    }
                    break;
                case 'top':
                case 'bottom':
                    this._middleColumn.resizeView(position, targetSize);
                    break;
            }
        }
        isEdgeGroupCollapsed(position) {
            var _a, _b;
            return (_b = (_a = this._getView(position)) === null || _a === void 0 ? void 0 : _a.isCollapsed) !== null && _b !== void 0 ? _b : false;
        }
        _getView(position) {
            switch (position) {
                case 'top':
                    return this._topView;
                case 'bottom':
                    return this._bottomView;
                case 'left':
                    return this._leftView;
                case 'right':
                    return this._rightView;
            }
        }
        toJSON() {
            const edgeGroups = {};
            if (this._leftView && this._leftIndex !== undefined) {
                edgeGroups.left = {
                    size: this._leftView.isCollapsed
                        ? this._leftView.lastExpandedSize
                        : this._outerSplitview.getViewSize(this._leftIndex),
                    visible: this._outerSplitview.isViewVisible(this._leftIndex),
                    collapsed: this._leftView.isCollapsed || undefined,
                };
            }
            if (this._rightView && this._rightIndex !== undefined) {
                edgeGroups.right = {
                    size: this._rightView.isCollapsed
                        ? this._rightView.lastExpandedSize
                        : this._outerSplitview.getViewSize(this._rightIndex),
                    visible: this._outerSplitview.isViewVisible(this._rightIndex),
                    collapsed: this._rightView.isCollapsed || undefined,
                };
            }
            if (this._topView) {
                edgeGroups.top = {
                    size: this._topView.isCollapsed
                        ? this._topView.lastExpandedSize
                        : this._middleColumn.getViewSize('top'),
                    visible: this._middleColumn.isViewVisible('top'),
                    collapsed: this._topView.isCollapsed || undefined,
                };
            }
            if (this._bottomView) {
                edgeGroups.bottom = {
                    size: this._bottomView.isCollapsed
                        ? this._bottomView.lastExpandedSize
                        : this._middleColumn.getViewSize('bottom'),
                    visible: this._middleColumn.isViewVisible('bottom'),
                    collapsed: this._bottomView.isCollapsed || undefined,
                };
            }
            return edgeGroups;
        }
        fromJSON(data) {
            var _a, _b, _c, _d, _e, _f, _g, _h, _j, _k, _l, _m, _o, _p, _q, _r, _s, _t, _u, _v;
            if (data.left && this._leftIndex !== undefined) {
                // Always restore the expanded size first. toJSON always records the
                // expanded size (even when collapsed), so restoredExpandedSize must
                // be applied before setCollapsed locks min/max to collapsedSize.
                (_a = this._leftView) === null || _a === void 0 ? void 0 : _a.restoreExpandedSize(data.left.size);
                (_b = this._leftView) === null || _b === void 0 ? void 0 : _b.setCollapsed((_c = data.left.collapsed) !== null && _c !== void 0 ? _c : false);
                this._outerSplitview.resizeView(this._leftIndex, data.left.collapsed
                    ? ((_e = (_d = this._leftView) === null || _d === void 0 ? void 0 : _d.collapsedSize) !== null && _e !== void 0 ? _e : data.left.size)
                    : data.left.size);
                if (!data.left.visible) {
                    this._outerSplitview.setViewVisible(this._leftIndex, false);
                }
            }
            if (data.right && this._rightIndex !== undefined) {
                (_f = this._rightView) === null || _f === void 0 ? void 0 : _f.restoreExpandedSize(data.right.size);
                (_g = this._rightView) === null || _g === void 0 ? void 0 : _g.setCollapsed((_h = data.right.collapsed) !== null && _h !== void 0 ? _h : false);
                this._outerSplitview.resizeView(this._rightIndex, data.right.collapsed
                    ? ((_k = (_j = this._rightView) === null || _j === void 0 ? void 0 : _j.collapsedSize) !== null && _k !== void 0 ? _k : data.right.size)
                    : data.right.size);
                if (!data.right.visible) {
                    this._outerSplitview.setViewVisible(this._rightIndex, false);
                }
            }
            if (data.top) {
                (_l = this._topView) === null || _l === void 0 ? void 0 : _l.restoreExpandedSize(data.top.size);
                (_m = this._topView) === null || _m === void 0 ? void 0 : _m.setCollapsed((_o = data.top.collapsed) !== null && _o !== void 0 ? _o : false);
                this._middleColumn.resizeView('top', data.top.collapsed
                    ? ((_q = (_p = this._topView) === null || _p === void 0 ? void 0 : _p.collapsedSize) !== null && _q !== void 0 ? _q : data.top.size)
                    : data.top.size);
                if (!data.top.visible) {
                    this._middleColumn.setViewVisible('top', false);
                }
            }
            if (data.bottom) {
                (_r = this._bottomView) === null || _r === void 0 ? void 0 : _r.restoreExpandedSize(data.bottom.size);
                (_s = this._bottomView) === null || _s === void 0 ? void 0 : _s.setCollapsed((_t = data.bottom.collapsed) !== null && _t !== void 0 ? _t : false);
                this._middleColumn.resizeView('bottom', data.bottom.collapsed
                    ? ((_v = (_u = this._bottomView) === null || _u === void 0 ? void 0 : _u.collapsedSize) !== null && _v !== void 0 ? _v : data.bottom.size)
                    : data.bottom.size);
                if (!data.bottom.visible) {
                    this._middleColumn.setViewVisible('bottom', false);
                }
            }
        }
        dispose() {
            var _a;
            this._disposables.dispose();
            (_a = this._shellElement.parentElement) === null || _a === void 0 ? void 0 : _a.removeChild(this._shellElement);
        }
    }

    const DEFAULT_ROOT_OVERLAY_MODEL = {
        activationSize: { type: 'pixels', value: 10 },
        size: { type: 'pixels', value: 20 },
    };
    function buildTabGroupColorPalette(options) {
        var _a;
        const entries = (_a = options.tabGroupColors) !== null && _a !== void 0 ? _a : DEFAULT_TAB_GROUP_COLORS;
        const enabled = options.tabGroupAccent !== 'off';
        return new TabGroupColorPalette(entries, enabled);
    }
    function moveGroupWithoutDestroying(options) {
        const activePanel = options.from.activePanel;
        const panels = [...options.from.panels].map((panel) => {
            const removedPanel = options.from.model.removePanel(panel);
            options.from.model.renderContainer.detatch(panel);
            return removedPanel;
        });
        panels.forEach((panel) => {
            options.to.model.openPanel(panel, {
                skipSetActive: activePanel !== panel,
                skipSetGroupActive: true,
            });
        });
    }
    class DockviewComponent extends BaseGrid {
        get orientation() {
            return this.gridview.orientation;
        }
        get totalPanels() {
            return this.panels.length;
        }
        get panels() {
            return this.groups.flatMap((group) => group.panels);
        }
        get options() {
            return this._options;
        }
        get tabGroupColorPalette() {
            return this._tabGroupColorPalette;
        }
        get activePanel() {
            const activeGroup = this.activeGroup;
            if (!activeGroup) {
                return undefined;
            }
            return activeGroup.activePanel;
        }
        get renderer() {
            var _a;
            return (_a = this.options.defaultRenderer) !== null && _a !== void 0 ? _a : 'onlyWhenVisible';
        }
        get defaultHeaderPosition() {
            var _a;
            return (_a = this.options.defaultHeaderPosition) !== null && _a !== void 0 ? _a : 'top';
        }
        get api() {
            return this._api;
        }
        get floatingGroups() {
            return this._floatingGroups;
        }
        /**
         * Promise that resolves when all popout groups from the last fromJSON call are restored.
         * Useful for tests that need to wait for delayed popout creation.
         */
        get popoutRestorationPromise() {
            return this._popoutRestorationPromise;
        }
        constructor(container, options) {
            var _a, _b, _c, _d, _e, _f, _g;
            super(container, {
                proportionalLayout: true,
                orientation: exports.Orientation.HORIZONTAL,
                styles: options.hideBorders
                    ? { separatorBorder: 'transparent' }
                    : undefined,
                disableAutoResizing: options.disableAutoResizing,
                locked: options.locked,
                margin: (_b = (_a = options.theme) === null || _a === void 0 ? void 0 : _a.gap) !== null && _b !== void 0 ? _b : 0,
                className: options.className,
            });
            this.nextGroupId = sequentialNumberGenerator();
            this._deserializer = new DefaultDockviewDeserialzier(this);
            this._watermark = null;
            this._popoutPopupServices = new Map();
            this._onWillDragPanel = new Emitter();
            this.onWillDragPanel = this._onWillDragPanel.event;
            this._onWillDragGroup = new Emitter();
            this.onWillDragGroup = this._onWillDragGroup.event;
            this._onDidDrop = new Emitter();
            this.onDidDrop = this._onDidDrop.event;
            this._onWillDrop = new Emitter();
            this.onWillDrop = this._onWillDrop.event;
            this._onWillShowOverlay = new Emitter();
            this.onWillShowOverlay = this._onWillShowOverlay.event;
            this._onUnhandledDragOverEvent = new Emitter();
            this.onUnhandledDragOverEvent = this._onUnhandledDragOverEvent.event;
            this._onDidRemovePanel = new Emitter();
            this.onDidRemovePanel = this._onDidRemovePanel.event;
            this._onDidAddPanel = new Emitter();
            this.onDidAddPanel = this._onDidAddPanel.event;
            this._onDidPopoutGroupSizeChange = new Emitter();
            this.onDidPopoutGroupSizeChange = this._onDidPopoutGroupSizeChange.event;
            this._onDidPopoutGroupPositionChange = new Emitter();
            this.onDidPopoutGroupPositionChange = this._onDidPopoutGroupPositionChange.event;
            this._onDidOpenPopoutWindowFail = new Emitter();
            this.onDidOpenPopoutWindowFail = this._onDidOpenPopoutWindowFail.event;
            this._onDidLayoutFromJSON = new Emitter();
            this.onDidLayoutFromJSON = this._onDidLayoutFromJSON.event;
            this._onDidActivePanelChange = new Emitter({ replay: true });
            this.onDidActivePanelChange = this._onDidActivePanelChange.event;
            this._onDidMovePanel = new Emitter();
            this.onDidMovePanel = this._onDidMovePanel.event;
            this._onDidCreateTabGroup = new Emitter();
            this.onDidCreateTabGroup = this._onDidCreateTabGroup.event;
            this._onDidDestroyTabGroup = new Emitter();
            this.onDidDestroyTabGroup = this._onDidDestroyTabGroup.event;
            this._onDidAddPanelToTabGroup = new Emitter();
            this.onDidAddPanelToTabGroup = this._onDidAddPanelToTabGroup.event;
            this._onDidRemovePanelFromTabGroup = new Emitter();
            this.onDidRemovePanelFromTabGroup = this._onDidRemovePanelFromTabGroup.event;
            this._onDidTabGroupChange = new Emitter();
            this.onDidTabGroupChange = this._onDidTabGroupChange.event;
            this._onDidTabGroupCollapsedChange = new Emitter();
            this.onDidTabGroupCollapsedChange = this._onDidTabGroupCollapsedChange.event;
            this._onDidMaximizedGroupChange = new Emitter();
            this.onDidMaximizedGroupChange = this._onDidMaximizedGroupChange.event;
            this._inShellLayout = false;
            this._edgeGroups = new Map();
            this._edgeGroupDisposables = new Map();
            this._floatingGroups = [];
            this._popoutGroups = [];
            this._popoutRestorationPromise = Promise.resolve();
            this._popoutRestorationCleanups = new Set();
            this._onDidRemoveGroup = new Emitter();
            this.onDidRemoveGroup = this._onDidRemoveGroup.event;
            this._onDidAddGroup = new Emitter();
            this.onDidAddGroup = this._onDidAddGroup.event;
            this._onDidOptionsChange = new Emitter();
            this.onDidOptionsChange = this._onDidOptionsChange.event;
            this._onDidActiveGroupChange = new Emitter();
            this.onDidActiveGroupChange = this._onDidActiveGroupChange.event;
            this._moving = false;
            this._options = options;
            this._tabGroupColorPalette = buildTabGroupColorPalette(options);
            this.popupService = new PopupService(this.element);
            this.contextMenuController = new ContextMenuController(this);
            this._api = new DockviewApi(this);
            // The shell always wraps the dockview element so edge groups can be
            // added at any time via addEdgeGroup() without re-parenting the DOM.
            this.disableResizing = true;
            container.removeChild(this.element);
            this._shellManager = new ShellManager(container, this.element, (w, h) => this._layoutFromShell(w, h), (_d = (_c = options.theme) === null || _c === void 0 ? void 0 : _c.gap) !== null && _d !== void 0 ? _d : 0, (_e = options.theme) === null || _e === void 0 ? void 0 : _e.edgeGroupCollapsedSize);
            // The shell wraps the dockview element, so move the popup anchor
            // into the shell so overflow dropdowns in edge groups position correctly
            this.popupService.updateRoot(this._shellManager.element);
            this._shellThemeClassnames = new Classnames(this._shellManager.element);
            // Anchor the overlay container to the shell element so that edge groups
            // (which live outside this.element in the shell layout) are covered when
            // dndOverlayMounting is 'absolute'.
            this.rootDropTargetContainer = new DropTargetAnchorContainer(this._shellManager.element, { disabled: true });
            this.overlayRenderContainer = new OverlayRenderContainer(this._shellManager.element, this);
            // Hosted in the shell (not inside `.dv-dockview`) so floating overlays
            // share a stacking context with `dv-render-overlay` panels; sized to
            // mirror the gridview rect so saved positions remain valid.
            this._floatingOverlayHost = document.createElement('div');
            this._floatingOverlayHost.className = 'dv-floating-overlay-host';
            this._shellManager.element.appendChild(this._floatingOverlayHost);
            const rootCanDisplayOverlay = (event, position) => {
                const data = getPanelData();
                if (data) {
                    if (data.viewId !== this.id) {
                        return false;
                    }
                    if (position === 'center') {
                        // center drop target is only allowed if there are no panels in the grid
                        // floating panels are allowed
                        return this.gridview.length === 0;
                    }
                    return true;
                }
                if (position === 'center' && this.gridview.length !== 0) {
                    /**
                     * for external events only show the four-corner drag overlays, disable
                     * the center position so that external drag events can fall through to the group
                     * and panel drop target handlers
                     */
                    return false;
                }
                const firedEvent = new DockviewUnhandledDragOverEvent(event, 'edge', position, getPanelData);
                this._onUnhandledDragOverEvent.fire(firedEvent);
                return firedEvent.isAccepted;
            };
            this._rootDropTarget = html5Backend.createDropTarget(this.element, {
                className: 'dv-drop-target-edge',
                canDisplayOverlay: rootCanDisplayOverlay,
                acceptedTargetZones: ['top', 'bottom', 'left', 'right', 'center'],
                overlayModel: (_f = options.rootOverlayModel) !== null && _f !== void 0 ? _f : DEFAULT_ROOT_OVERLAY_MODEL,
                getOverrideTarget: () => { var _a; return (_a = this.rootDropTargetContainer) === null || _a === void 0 ? void 0 : _a.model; },
            });
            this._rootPointerDropTarget = pointerBackend.createDropTarget(this.element, {
                className: 'dv-drop-target-edge',
                canDisplayOverlay: rootCanDisplayOverlay,
                acceptedTargetZones: [
                    'top',
                    'bottom',
                    'left',
                    'right',
                    'center',
                ],
                overlayModel: (_g = options.rootOverlayModel) !== null && _g !== void 0 ? _g : DEFAULT_ROOT_OVERLAY_MODEL,
                getOverrideTarget: () => { var _a; return (_a = this.rootDropTargetContainer) === null || _a === void 0 ? void 0 : _a.model; },
            });
            this.updateDropTargetModel(options);
            toggleClass(this.gridview.element, 'dv-dockview', true);
            toggleClass(this.element, 'dv-debug', !!options.debug);
            this.updateTheme();
            this.updateWatermark();
            if (options.debug) {
                this.addDisposables(new StrictEventsSequencing(this));
            }
            this.addDisposables(this.rootDropTargetContainer, this.overlayRenderContainer, this._onWillDragPanel, this._onWillDragGroup, this._onWillShowOverlay, this._onDidActivePanelChange, this._onDidAddPanel, this._onDidRemovePanel, this._onDidLayoutFromJSON, this._onDidDrop, this._onWillDrop, this._onDidMovePanel, this._onDidMovePanel.event(() => {
                /**
                 * Update overlay positions after DOM layout completes to prevent 0×0 dimensions.
                 * With defaultRenderer="always" this results in panel content not showing after move operations.
                 * Debounced to avoid multiple calls when moving groups with multiple panels.
                 */
                this.debouncedUpdateAllPositions();
            }), this._onDidAddGroup, this._onDidRemoveGroup, this._onDidActiveGroupChange, this._onUnhandledDragOverEvent, this._onDidMaximizedGroupChange, this._onDidOptionsChange, this._onDidPopoutGroupSizeChange, this._onDidPopoutGroupPositionChange, this._onDidOpenPopoutWindowFail, this._onDidCreateTabGroup, this._onDidDestroyTabGroup, this._onDidAddPanelToTabGroup, this._onDidRemovePanelFromTabGroup, this._onDidTabGroupChange, this._onDidTabGroupCollapsedChange, this.onDidViewVisibilityChangeMicroTaskQueue(() => {
                this.updateWatermark();
            }), this.onDidAdd((event) => {
                if (!this._moving) {
                    this._onDidAddGroup.fire(event);
                }
            }), this.onDidRemove((event) => {
                if (!this._moving) {
                    this._onDidRemoveGroup.fire(event);
                }
            }), this.onDidActiveChange((event) => {
                if (!this._moving) {
                    this._onDidActiveGroupChange.fire(event);
                }
            }), this.onDidMaximizedChange((event) => {
                this._onDidMaximizedGroupChange.fire({
                    group: event.panel,
                    isMaximized: event.isMaximized,
                });
            }), exports.DockviewEvent.any(this.onDidAdd, this.onDidRemove)(() => {
                this.updateWatermark();
            }), exports.DockviewEvent.any(this.onDidAddPanel, this.onDidRemovePanel, this.onDidAddGroup, this.onDidRemove, this.onDidRemoveGroup, this.onDidMovePanel, this.onDidActivePanelChange, this.onDidPopoutGroupPositionChange, this.onDidPopoutGroupSizeChange, this.onDidCreateTabGroup, this.onDidDestroyTabGroup, this.onDidAddPanelToTabGroup, this.onDidRemovePanelFromTabGroup, this.onDidTabGroupChange, this.onDidTabGroupCollapsedChange)(() => {
                this._bufferOnDidLayoutChange.fire();
            }), exports.DockviewDisposable.from(() => {
                var _a;
                // Cancel any pending popout-restoration timers scheduled by
                // fromJSON so they don't open new browser windows after
                // dispose, and resolve their promises so callers awaiting
                // popoutRestorationPromise don't hang. See issue #851.
                for (const cleanup of [...this._popoutRestorationCleanups]) {
                    cleanup();
                }
                this._popoutRestorationCleanups.clear();
                // iterate over a copy of the array since .dispose() mutates the original array
                for (const group of [...this._floatingGroups]) {
                    group.dispose();
                }
                // iterate over a copy of the array since .dispose() mutates the original array
                for (const group of [...this._popoutGroups]) {
                    group.disposable.dispose();
                }
                (_a = this._shellManager) === null || _a === void 0 ? void 0 : _a.dispose();
                for (const d of this._edgeGroupDisposables.values()) {
                    d.dispose();
                }
                this._edgeGroupDisposables.clear();
            }), this._rootDropTarget, this._rootPointerDropTarget, exports.DockviewEvent.any(this._rootDropTarget.onWillShowOverlay, this._rootPointerDropTarget.onWillShowOverlay)((event) => {
                if (this.gridview.length > 0 && event.position === 'center') {
                    // option only available when no panels in primary grid
                    return;
                }
                this._onWillShowOverlay.fire(new DockviewWillShowOverlayLocationEvent(event, {
                    kind: 'edge',
                    panel: undefined,
                    api: this._api,
                    group: undefined,
                    getData: getPanelData,
                }));
            }), exports.DockviewEvent.any(this._rootDropTarget.onDrop, this._rootPointerDropTarget.onDrop)((event) => {
                var _a;
                const willDropEvent = new DockviewWillDropEvent({
                    nativeEvent: event.nativeEvent,
                    position: event.position,
                    panel: undefined,
                    api: this._api,
                    group: undefined,
                    getData: getPanelData,
                    kind: 'edge',
                });
                this._onWillDrop.fire(willDropEvent);
                if (willDropEvent.defaultPrevented) {
                    return;
                }
                const data = getPanelData();
                if (data) {
                    this.moveGroupOrPanel({
                        from: {
                            groupId: data.groupId,
                            panelId: (_a = data.panelId) !== null && _a !== void 0 ? _a : undefined,
                        },
                        to: {
                            group: this.orthogonalize(event.position),
                            position: 'center',
                        },
                    });
                }
                else {
                    this._onDidDrop.fire(new DockviewDidDropEvent({
                        nativeEvent: event.nativeEvent,
                        position: event.position,
                        panel: undefined,
                        api: this._api,
                        group: undefined,
                        getData: getPanelData,
                    }));
                }
            }));
        }
        setVisible(panel, visible) {
            switch (panel.api.location.type) {
                case 'grid':
                    super.setVisible(panel, visible);
                    break;
                case 'floating': {
                    const item = this.floatingGroups.find((floatingGroup) => floatingGroup.group === panel);
                    if (item) {
                        item.overlay.setVisible(visible);
                        panel.api._onDidVisibilityChange.fire({
                            isVisible: visible,
                        });
                    }
                    break;
                }
                case 'popout':
                    console.warn('dockview: You cannot hide a group that is in a popout window');
                    break;
            }
        }
        /**
         * Returns the {@link PopupService} that should host popovers (context
         * menus, tab overflow menus) for the given group. Popout groups have their
         * own service rooted in their popout window so the popover renders there
         * and dismisses on events from that window.
         */
        getPopupServiceForGroup(group) {
            var _a;
            return (_a = this._popoutPopupServices.get(group.id)) !== null && _a !== void 0 ? _a : this.popupService;
        }
        addPopoutGroup(itemToPopout, options) {
            var _a, _b, _c, _d, _e, _f;
            if (itemToPopout instanceof DockviewGroupPanel &&
                itemToPopout.model.location.type === 'edge') {
                // edge groups are permanent structural elements and cannot be popped out
                return Promise.resolve(false);
            }
            if (itemToPopout instanceof DockviewPanel &&
                itemToPopout.group.size === 1) {
                return this.addPopoutGroup(itemToPopout.group, options);
            }
            const theme = getDockviewTheme(this.gridview.element);
            const element = this.element;
            function getBox() {
                if (options === null || options === void 0 ? void 0 : options.position) {
                    return options.position;
                }
                if (itemToPopout instanceof DockviewGroupPanel) {
                    return itemToPopout.element.getBoundingClientRect();
                }
                if (itemToPopout.group) {
                    return itemToPopout.group.element.getBoundingClientRect();
                }
                return element.getBoundingClientRect();
            }
            const box = getBox();
            const groupId = (_b = (_a = options === null || options === void 0 ? void 0 : options.overridePopoutGroup) === null || _a === void 0 ? void 0 : _a.id) !== null && _b !== void 0 ? _b : this.getNextGroupId();
            const _window = new PopoutWindow(`${this.id}-${groupId}`, // unique id
            theme !== null && theme !== void 0 ? theme : '', {
                url: (_e = (_c = options === null || options === void 0 ? void 0 : options.popoutUrl) !== null && _c !== void 0 ? _c : (_d = this.options) === null || _d === void 0 ? void 0 : _d.popoutUrl) !== null && _e !== void 0 ? _e : '/popout.html',
                left: window.screenX + box.left,
                top: window.screenY + box.top,
                width: box.width,
                height: box.height,
                onDidOpen: options === null || options === void 0 ? void 0 : options.onDidOpen,
                onWillClose: options === null || options === void 0 ? void 0 : options.onWillClose,
                nonce: (_f = this.options) === null || _f === void 0 ? void 0 : _f.nonce,
            });
            const popoutWindowDisposable = new CompositeDisposable(_window, _window.onDidClose(() => {
                popoutWindowDisposable.dispose();
            }));
            return _window
                .open()
                .then((popoutContainer) => {
                var _a;
                if (_window.isDisposed) {
                    return false;
                }
                const referenceGroup = (options === null || options === void 0 ? void 0 : options.referenceGroup)
                    ? options.referenceGroup
                    : itemToPopout instanceof DockviewPanel
                        ? itemToPopout.group
                        : itemToPopout;
                const referenceLocation = itemToPopout.api.location.type;
                /**
                 * The group that is being added doesn't already exist within the DOM, the most likely occurrence
                 * of this case is when being called from the `fromJSON(...)` method
                 */
                const isGroupAddedToDom = referenceGroup.element.parentElement !== null;
                let group;
                if (!isGroupAddedToDom) {
                    group = referenceGroup;
                }
                else if (options === null || options === void 0 ? void 0 : options.overridePopoutGroup) {
                    group = options.overridePopoutGroup;
                }
                else {
                    group = this.createGroup({ id: groupId });
                    if (popoutContainer) {
                        this._onDidAddGroup.fire(group);
                    }
                }
                if (popoutContainer === null) {
                    console.error('dockview: failed to create popout. perhaps you need to allow pop-ups for this website');
                    popoutWindowDisposable.dispose();
                    this._onDidOpenPopoutWindowFail.fire();
                    // if the popout window was blocked, we need to move the group back to the reference group
                    // and set it to visible
                    this.movingLock(() => moveGroupWithoutDestroying({
                        from: group,
                        to: referenceGroup,
                    }));
                    if (!referenceGroup.api.isVisible) {
                        referenceGroup.api.setVisible(true);
                    }
                    return false;
                }
                const gready = document.createElement('div');
                gready.className = 'dv-overlay-render-container';
                const overlayRenderContainer = new OverlayRenderContainer(gready, this);
                group.model.renderContainer = overlayRenderContainer;
                group.layout(_window.window.innerWidth, _window.window.innerHeight);
                let floatingBox;
                if (!(options === null || options === void 0 ? void 0 : options.overridePopoutGroup) && isGroupAddedToDom) {
                    if (itemToPopout instanceof DockviewPanel) {
                        this.movingLock(() => {
                            const panel = referenceGroup.model.removePanel(itemToPopout);
                            group.model.openPanel(panel);
                        });
                    }
                    else {
                        this.movingLock(() => moveGroupWithoutDestroying({
                            from: referenceGroup,
                            to: group,
                        }));
                        switch (referenceLocation) {
                            case 'grid':
                                referenceGroup.api.setVisible(false);
                                break;
                            case 'floating':
                            case 'popout':
                                floatingBox = (_a = this._floatingGroups
                                    .find((value) => value.group.api.id ===
                                    itemToPopout.api.id)) === null || _a === void 0 ? void 0 : _a.overlay.toJSON();
                                this.removeGroup(referenceGroup);
                                break;
                        }
                    }
                }
                popoutContainer.classList.add('dv-dockview');
                popoutContainer.style.overflow = 'hidden';
                popoutContainer.appendChild(gready);
                popoutContainer.appendChild(group.element);
                const anchor = document.createElement('div');
                const dropTargetContainer = new DropTargetAnchorContainer(anchor, { disabled: this.rootDropTargetContainer.disabled });
                popoutContainer.appendChild(anchor);
                group.model.dropTargetContainer = dropTargetContainer;
                // Each popout group needs its own popover service so that
                // tab context menus, chip menus, and tab overflow menus
                // render in the popout window (not the main window) and
                // their pointerdown/keydown listeners fire on the right
                // window for outside-click and Escape dismissal.
                const popoutPopupService = new PopupService(popoutContainer, _window.window);
                this._popoutPopupServices.set(group.id, popoutPopupService);
                popoutWindowDisposable.addDisposables(popoutPopupService, exports.DockviewDisposable.from(() => {
                    this._popoutPopupServices.delete(group.id);
                }));
                group.model.location = {
                    type: 'popout',
                    getWindow: () => _window.window,
                    popoutUrl: options === null || options === void 0 ? void 0 : options.popoutUrl,
                };
                if (isGroupAddedToDom &&
                    itemToPopout.api.location.type === 'grid') {
                    itemToPopout.api.setVisible(false);
                }
                this.doSetGroupAndPanelActive(group);
                popoutWindowDisposable.addDisposables(group.api.onDidActiveChange((event) => {
                    var _a;
                    if (event.isActive) {
                        (_a = _window.window) === null || _a === void 0 ? void 0 : _a.focus();
                    }
                }), group.api.onWillFocus(() => {
                    var _a;
                    (_a = _window.window) === null || _a === void 0 ? void 0 : _a.focus();
                }));
                let returnedGroup;
                const isValidReferenceGroup = isGroupAddedToDom &&
                    referenceGroup &&
                    this.getPanel(referenceGroup.id);
                const value = {
                    window: _window,
                    popoutGroup: group,
                    referenceGroup: isValidReferenceGroup
                        ? referenceGroup.id
                        : undefined,
                    disposable: {
                        dispose: () => {
                            popoutWindowDisposable.dispose();
                            return returnedGroup;
                        },
                    },
                };
                const _onDidWindowPositionChange = onDidWindowMoveEnd(_window.window);
                popoutWindowDisposable.addDisposables(_onDidWindowPositionChange, onDidWindowResizeEnd(_window.window, () => {
                    this._onDidPopoutGroupSizeChange.fire({
                        width: _window.window.innerWidth,
                        height: _window.window.innerHeight,
                        group,
                    });
                }), _onDidWindowPositionChange.event(() => {
                    this._onDidPopoutGroupPositionChange.fire({
                        screenX: _window.window.screenX,
                        screenY: _window.window.screenX,
                        group,
                    });
                }),
                /**
                 * ResizeObserver seems slow here, I do not know why but we don't need it
                 * since we can reply on the window resize event as we will occupy the full
                 * window dimensions
                 */
                addDisposableListener(_window.window, 'resize', () => {
                    group.layout(_window.window.innerWidth, _window.window.innerHeight);
                }), overlayRenderContainer, exports.DockviewDisposable.from(() => {
                    if (this.isDisposed) {
                        return; // cleanup may run after instance is disposed
                    }
                    if (isGroupAddedToDom &&
                        this.getPanel(referenceGroup.id)) {
                        this.movingLock(() => moveGroupWithoutDestroying({
                            from: group,
                            to: referenceGroup,
                        }));
                        if (!referenceGroup.api.isVisible) {
                            referenceGroup.api.setVisible(true);
                        }
                        if (this.getPanel(group.id)) {
                            this.doRemoveGroup(group, {
                                skipPopoutAssociated: true,
                            });
                        }
                    }
                    else if (this.getPanel(group.id)) {
                        group.model.renderContainer =
                            this.overlayRenderContainer;
                        group.model.dropTargetContainer =
                            this.rootDropTargetContainer;
                        returnedGroup = group;
                        const alreadyRemoved = !this._popoutGroups.find((p) => p.popoutGroup === group);
                        if (alreadyRemoved) {
                            /**
                             * If this popout group was explicitly removed then we shouldn't run the additional
                             * steps. To tell if the running of this disposable is the result of this popout group
                             * being explicitly removed we can check if this popout group is still referenced in
                             * the `this._popoutGroups` list.
                             */
                            return;
                        }
                        if (floatingBox) {
                            this.addFloatingGroup(group, {
                                height: floatingBox.height,
                                width: floatingBox.width,
                                position: floatingBox,
                            });
                        }
                        else {
                            this.doRemoveGroup(group, {
                                skipDispose: true,
                                skipActive: true,
                                skipPopoutReturn: true,
                            });
                            group.model.location = { type: 'grid' };
                            this.movingLock(() => {
                                // suppress group add events since the group already exists
                                this.doAddGroup(group, [0]);
                            });
                        }
                        this.doSetGroupAndPanelActive(group);
                    }
                }));
                this._popoutGroups.push(value);
                this.updateWatermark();
                return true;
            })
                .catch((err) => {
                console.error('dockview: failed to create popout.', err);
                return false;
            });
        }
        addFloatingGroup(item, options) {
            var _a, _b, _c, _d, _e, _f;
            if (item instanceof DockviewGroupPanel &&
                item.model.location.type === 'edge') {
                // edge groups are permanent structural elements and cannot be floated
                return;
            }
            let group;
            if (item instanceof DockviewPanel) {
                group = this.createGroup();
                this._onDidAddGroup.fire(group);
                this.movingLock(() => this.removePanel(item, {
                    removeEmptyGroup: true,
                    skipDispose: true,
                    skipSetActiveGroup: true,
                }));
                this.movingLock(() => group.model.openPanel(item, { skipSetGroupActive: true }));
            }
            else {
                group = item;
                const popoutReferenceGroupId = (_a = this._popoutGroups.find((_) => _.popoutGroup === group)) === null || _a === void 0 ? void 0 : _a.referenceGroup;
                const popoutReferenceGroup = popoutReferenceGroupId
                    ? this.getPanel(popoutReferenceGroupId)
                    : undefined;
                const skip = typeof (options === null || options === void 0 ? void 0 : options.skipRemoveGroup) === 'boolean' &&
                    options.skipRemoveGroup;
                if (!skip) {
                    if (popoutReferenceGroup) {
                        this.movingLock(() => moveGroupWithoutDestroying({
                            from: item,
                            to: popoutReferenceGroup,
                        }));
                        this.doRemoveGroup(item, {
                            skipPopoutReturn: true,
                            skipPopoutAssociated: true,
                        });
                        this.doRemoveGroup(popoutReferenceGroup, {
                            skipDispose: true,
                        });
                        group = popoutReferenceGroup;
                    }
                    else {
                        this.doRemoveGroup(item, {
                            skipDispose: true,
                            skipPopoutReturn: true,
                            skipPopoutAssociated: false,
                        });
                    }
                }
            }
            function getAnchoredBox() {
                if (options === null || options === void 0 ? void 0 : options.position) {
                    const result = {};
                    if ('left' in options.position) {
                        result.left = Math.max(options.position.left, 0);
                    }
                    else if ('right' in options.position) {
                        result.right = Math.max(options.position.right, 0);
                    }
                    else {
                        result.left = DEFAULT_FLOATING_GROUP_POSITION.left;
                    }
                    if ('top' in options.position) {
                        result.top = Math.max(options.position.top, 0);
                    }
                    else if ('bottom' in options.position) {
                        result.bottom = Math.max(options.position.bottom, 0);
                    }
                    else {
                        result.top = DEFAULT_FLOATING_GROUP_POSITION.top;
                    }
                    if (typeof options.width === 'number') {
                        result.width = Math.max(options.width, 0);
                    }
                    else {
                        result.width = DEFAULT_FLOATING_GROUP_POSITION.width;
                    }
                    if (typeof options.height === 'number') {
                        result.height = Math.max(options.height, 0);
                    }
                    else {
                        result.height = DEFAULT_FLOATING_GROUP_POSITION.height;
                    }
                    return result;
                }
                return {
                    left: typeof (options === null || options === void 0 ? void 0 : options.x) === 'number'
                        ? Math.max(options.x, 0)
                        : DEFAULT_FLOATING_GROUP_POSITION.left,
                    top: typeof (options === null || options === void 0 ? void 0 : options.y) === 'number'
                        ? Math.max(options.y, 0)
                        : DEFAULT_FLOATING_GROUP_POSITION.top,
                    width: typeof (options === null || options === void 0 ? void 0 : options.width) === 'number'
                        ? Math.max(options.width, 0)
                        : DEFAULT_FLOATING_GROUP_POSITION.width,
                    height: typeof (options === null || options === void 0 ? void 0 : options.height) === 'number'
                        ? Math.max(options.height, 0)
                        : DEFAULT_FLOATING_GROUP_POSITION.height,
                };
            }
            const anchoredBox = getAnchoredBox();
            const overlay = new Overlay(Object.assign(Object.assign({ container: (_b = this._floatingOverlayHost) !== null && _b !== void 0 ? _b : this.gridview.element, content: group.element }, anchoredBox), { minimumInViewportWidth: this.options.floatingGroupBounds === 'boundedWithinViewport'
                    ? undefined
                    : ((_d = (_c = this.options.floatingGroupBounds) === null || _c === void 0 ? void 0 : _c.minimumWidthWithinViewport) !== null && _d !== void 0 ? _d : DEFAULT_FLOATING_GROUP_OVERFLOW_SIZE), minimumInViewportHeight: this.options.floatingGroupBounds === 'boundedWithinViewport'
                    ? undefined
                    : ((_f = (_e = this.options.floatingGroupBounds) === null || _e === void 0 ? void 0 : _e.minimumHeightWithinViewport) !== null && _f !== void 0 ? _f : DEFAULT_FLOATING_GROUP_OVERFLOW_SIZE) }));
            const el = group.element.querySelector('.dv-void-container');
            if (!el) {
                throw new Error('dockview: failed to find drag handle');
            }
            overlay.setupDrag(el, {
                inDragMode: typeof (options === null || options === void 0 ? void 0 : options.inDragMode) === 'boolean'
                    ? options.inDragMode
                    : false,
            });
            const floatingGroupPanel = new DockviewFloatingGroupPanel(group, overlay);
            const disposable = new CompositeDisposable(group.api.onDidActiveChange((event) => {
                if (event.isActive) {
                    overlay.bringToFront();
                }
            }), (() => {
                let lastWidth = -1;
                let lastHeight = -1;
                return watchElementResize(group.element, (entry) => {
                    const width = Math.round(entry.contentRect.width);
                    const height = Math.round(entry.contentRect.height);
                    if (width === lastWidth && height === lastHeight) {
                        return;
                    }
                    lastWidth = width;
                    lastHeight = height;
                    group.layout(width, height); // let the group know it's size is changing so it can fire events to the panel
                });
            })());
            floatingGroupPanel.addDisposables(overlay.onDidChange(() => {
                // this is either a resize or a move
                // to inform the panels .layout(...) the group with it's current size
                // don't care about resize since the above watcher handles that
                group.layout(group.width, group.height);
            }), overlay.onDidChangeEnd(() => {
                this._bufferOnDidLayoutChange.fire();
            }), group.onDidChange((event) => {
                overlay.setBounds({
                    height: event === null || event === void 0 ? void 0 : event.height,
                    width: event === null || event === void 0 ? void 0 : event.width,
                });
            }), {
                dispose: () => {
                    disposable.dispose();
                    remove(this._floatingGroups, floatingGroupPanel);
                    group.model.location = { type: 'grid' };
                    this.updateWatermark();
                },
            });
            this._floatingGroups.push(floatingGroupPanel);
            group.model.location = { type: 'floating' };
            if (!(options === null || options === void 0 ? void 0 : options.skipActiveGroup)) {
                this.doSetGroupAndPanelActive(group);
            }
            this.updateWatermark();
        }
        orthogonalize(position, options) {
            this.gridview.normalize();
            switch (position) {
                case 'top':
                case 'bottom':
                    if (this.gridview.orientation === exports.Orientation.HORIZONTAL) {
                        // we need to add to a vertical splitview but the current root is a horizontal splitview.
                        // insert a vertical splitview at the root level and add the existing view as a child
                        this.gridview.insertOrthogonalSplitviewAtRoot();
                    }
                    break;
                case 'left':
                case 'right':
                    if (this.gridview.orientation === exports.Orientation.VERTICAL) {
                        // we need to add to a horizontal splitview but the current root is a vertical splitview.
                        // insert a horiziontal splitview at the root level and add the existing view as a child
                        this.gridview.insertOrthogonalSplitviewAtRoot();
                    }
                    break;
            }
            switch (position) {
                case 'top':
                case 'left':
                case 'center':
                    return this.createGroupAtLocation([0], undefined, options); // insert into first position
                case 'bottom':
                case 'right':
                    return this.createGroupAtLocation([this.gridview.length], undefined, options); // insert into last position
                default:
                    throw new Error(`dockview: unsupported position ${position}`);
            }
        }
        updateOptions(options) {
            var _a, _b, _c, _d, _e;
            super.updateOptions(options);
            if ('floatingGroupBounds' in options) {
                for (const group of this._floatingGroups) {
                    switch (options.floatingGroupBounds) {
                        case 'boundedWithinViewport':
                            group.overlay.minimumInViewportHeight = undefined;
                            group.overlay.minimumInViewportWidth = undefined;
                            break;
                        case undefined:
                            group.overlay.minimumInViewportHeight =
                                DEFAULT_FLOATING_GROUP_OVERFLOW_SIZE;
                            group.overlay.minimumInViewportWidth =
                                DEFAULT_FLOATING_GROUP_OVERFLOW_SIZE;
                            break;
                        default:
                            group.overlay.minimumInViewportHeight =
                                (_a = options.floatingGroupBounds) === null || _a === void 0 ? void 0 : _a.minimumHeightWithinViewport;
                            group.overlay.minimumInViewportWidth =
                                (_b = options.floatingGroupBounds) === null || _b === void 0 ? void 0 : _b.minimumWidthWithinViewport;
                    }
                    group.overlay.setBounds();
                }
            }
            this.updateDropTargetModel(options);
            const oldDisableDnd = this.options.disableDnd;
            const oldDndStrategy = this.options.dndStrategy;
            this._options = Object.assign(Object.assign({}, this.options), options);
            const newDisableDnd = this.options.disableDnd;
            const newDndStrategy = this.options.dndStrategy;
            if (oldDisableDnd !== newDisableDnd ||
                oldDndStrategy !== newDndStrategy) {
                this.updateDragAndDropState();
            }
            if ('theme' in options) {
                this.updateTheme();
            }
            if ('createRightHeaderActionComponent' in options ||
                'createLeftHeaderActionComponent' in options ||
                'createPrefixHeaderActionComponent' in options) {
                for (const group of this.groups) {
                    group.model.updateHeaderActions();
                }
            }
            if ('createWatermarkComponent' in options) {
                if (this._watermark) {
                    this._watermark.element.parentElement.remove();
                    (_d = (_c = this._watermark).dispose) === null || _d === void 0 ? void 0 : _d.call(_c);
                    this._watermark = null;
                }
                this.updateWatermark();
                for (const group of this.groups) {
                    group.model.refreshWatermark();
                }
            }
            if ('tabGroupColors' in options || 'tabGroupAccent' in options) {
                this._tabGroupColorPalette.setEntries((_e = this._options.tabGroupColors) !== null && _e !== void 0 ? _e : DEFAULT_TAB_GROUP_COLORS);
                this._tabGroupColorPalette.enabled =
                    this._options.tabGroupAccent !== 'off';
                for (const group of this.groups) {
                    group.model.refreshTabGroupAccent();
                }
            }
            this._onDidOptionsChange.fire();
            this._layoutFromShell(this.gridview.width, this.gridview.height);
        }
        layout(width, height, forceResize) {
            if (this._shellManager && !this._inShellLayout) {
                this._shellManager.layout(width, height);
            }
            else {
                super.layout(width, height, forceResize);
            }
            this._syncFloatingOverlayHost();
            if (this._floatingGroups) {
                for (const floating of this._floatingGroups) {
                    // ensure floting groups stay within visible boundaries
                    floating.overlay.setBounds();
                }
            }
        }
        _syncFloatingOverlayHost() {
            if (!this._floatingOverlayHost || !this._shellManager) {
                return;
            }
            const shellRect = this._shellManager.element.getBoundingClientRect();
            const gridRect = this.element.getBoundingClientRect();
            const host = this._floatingOverlayHost;
            host.style.left = `${gridRect.left - shellRect.left}px`;
            host.style.top = `${gridRect.top - shellRect.top}px`;
            host.style.width = `${gridRect.width}px`;
            host.style.height = `${gridRect.height}px`;
        }
        _layoutFromShell(width, height) {
            this._inShellLayout = true;
            this.layout(width, height, true);
            this._inShellLayout = false;
        }
        forceRelayout() {
            if (this._shellManager) {
                this._layoutFromShell(this.width, this.height);
            }
            else {
                super.forceRelayout();
            }
        }
        addEdgeGroup(position, options) {
            if (this._edgeGroups.has(position)) {
                throw new Error(`dockview: edge group already exists at position '${position}'`);
            }
            const group = this.createGroup({ id: options.id });
            group.model.location = { type: 'edge', position };
            group.model.headerPosition = position;
            this._edgeGroups.set(position, group);
            this._onDidAddGroup.fire(group);
            // Collapse when the group becomes empty
            const autoCollapseDisposable = group.model.onDidRemovePanel(() => {
                if (group.model.isEmpty) {
                    this.setEdgeGroupCollapsed(group, true);
                }
            });
            this._edgeGroupDisposables.set(position, autoCollapseDisposable);
            this._shellManager.addEdgeView(position, options, group);
            return group.api;
        }
        getEdgeGroup(position) {
            var _a;
            return (_a = this._edgeGroups.get(position)) === null || _a === void 0 ? void 0 : _a.api;
        }
        setEdgeGroupVisible(position, visible) {
            this._shellManager.setEdgeGroupVisible(position, visible);
        }
        isEdgeGroupVisible(position) {
            return this._shellManager.isEdgeGroupVisible(position);
        }
        removeEdgeGroup(position) {
            var _a;
            const group = this._edgeGroups.get(position);
            if (!group) {
                throw new Error(`dockview: no edge group exists at position '${position}'`);
            }
            // Remove panels inside the group first
            for (const panel of [...group.panels]) {
                this.removePanel(panel, {
                    removeEmptyGroup: false,
                    skipDispose: false,
                });
            }
            // Dispose the auto-collapse listener
            (_a = this._edgeGroupDisposables.get(position)) === null || _a === void 0 ? void 0 : _a.dispose();
            this._edgeGroupDisposables.delete(position);
            // Remove from the shell splitview
            this._shellManager.removeEdgeView(position);
            // Clean up group state
            this._edgeGroups.delete(position);
            group.dispose();
            this._groups.delete(group.id);
            this._onDidRemoveGroup.fire(group);
        }
        setEdgeGroupCollapsed(group, collapsed) {
            for (const [position, edgeGroup] of this._edgeGroups) {
                if (edgeGroup === group) {
                    if (this._shellManager.isEdgeGroupCollapsed(position) ===
                        collapsed) {
                        // Skip the splitview resize on a no-op: with non-zero
                        // theme gap, redundant resizeView calls accumulate
                        // rounding drift that gradually shrinks the group.
                        return;
                    }
                    this._shellManager.setEdgeGroupCollapsed(position, collapsed);
                    edgeGroup.api._onDidCollapsedChange.fire({
                        isCollapsed: collapsed,
                    });
                    return;
                }
            }
        }
        isEdgeGroupCollapsed(group) {
            for (const [position, edgeGroup] of this._edgeGroups) {
                if (edgeGroup === group) {
                    return this._shellManager.isEdgeGroupCollapsed(position);
                }
            }
            return false;
        }
        updateDragAndDropState() {
            // Update draggable state for all tabs and void containers
            for (const group of this.groups) {
                group.model.updateDragAndDropState();
            }
        }
        focus() {
            var _a;
            (_a = this.activeGroup) === null || _a === void 0 ? void 0 : _a.focus();
        }
        getGroupPanel(id) {
            return this.panels.find((panel) => panel.id === id);
        }
        setActivePanel(panel) {
            panel.group.model.openPanel(panel);
            this.doSetGroupAndPanelActive(panel.group);
        }
        moveToNext(options = {}) {
            var _a;
            if (!options.group) {
                if (!this.activeGroup) {
                    return;
                }
                options.group = this.activeGroup;
            }
            if (options.includePanel && options.group) {
                if (options.group.activePanel !==
                    options.group.panels[options.group.panels.length - 1]) {
                    options.group.model.moveToNext({ suppressRoll: true });
                    return;
                }
            }
            const location = getGridLocation(options.group.element);
            const next = (_a = this.gridview.next(location)) === null || _a === void 0 ? void 0 : _a.view;
            this.doSetGroupAndPanelActive(next);
        }
        moveToPrevious(options = {}) {
            var _a;
            if (!options.group) {
                if (!this.activeGroup) {
                    return;
                }
                options.group = this.activeGroup;
            }
            if (options.includePanel && options.group) {
                if (options.group.activePanel !== options.group.panels[0]) {
                    options.group.model.moveToPrevious({ suppressRoll: true });
                    return;
                }
            }
            const location = getGridLocation(options.group.element);
            const next = (_a = this.gridview.previous(location)) === null || _a === void 0 ? void 0 : _a.view;
            if (next) {
                this.doSetGroupAndPanelActive(next);
            }
        }
        /**
         * Serialize the current state of the layout
         *
         * @returns A JSON respresentation of the layout
         */
        toJSON() {
            var _a;
            const data = this.gridview.serialize();
            const panels = this.panels.reduce((collection, panel) => {
                collection[panel.id] = panel.toJSON();
                return collection;
            }, {});
            const floats = this._floatingGroups.map((group) => {
                return {
                    data: group.group.toJSON(),
                    position: group.overlay.toJSON(),
                };
            });
            const popoutGroups = this._popoutGroups.map((group) => {
                return {
                    data: group.popoutGroup.toJSON(),
                    gridReferenceGroup: group.referenceGroup,
                    position: group.window.dimensions(),
                    url: group.popoutGroup.api.location.type === 'popout'
                        ? group.popoutGroup.api.location.popoutUrl
                        : undefined,
                };
            });
            const result = {
                grid: data,
                panels,
                activeGroup: (_a = this.activeGroup) === null || _a === void 0 ? void 0 : _a.id,
            };
            if (floats.length > 0) {
                result.floatingGroups = floats;
            }
            if (popoutGroups.length > 0) {
                result.popoutGroups = popoutGroups;
            }
            if (this._edgeGroups.size > 0) {
                const shellSerialized = this._shellManager.toJSON();
                // Augment each entry with the serialized group state
                for (const [position, group] of this._edgeGroups) {
                    const entry = shellSerialized[position];
                    if (entry) {
                        entry.group = group.toJSON();
                    }
                }
                result.edgeGroups = shellSerialized;
            }
            return result;
        }
        fromJSON(data, options) {
            var _a, _b, _c;
            // Cancel any popout-restoration timers queued by a previous fromJSON
            // that haven't fired yet. Each cleanup also disposes the orphan group
            // that was registered in _groups synchronously but never parented
            // into a popout window — otherwise the upcoming clear() would call
            // gridview.remove() on an unparented element and throw
            // "Invalid grid element". See issue #1304.
            for (const cleanup of [...this._popoutRestorationCleanups]) {
                cleanup();
            }
            this._popoutRestorationCleanups.clear();
            const existingPanels = new Map();
            let tempGroup;
            if (options === null || options === void 0 ? void 0 : options.reuseExistingPanels) {
                /**
                 * What are we doing here?
                 *
                 * 1. Create a temporary group to hold any panels that currently exist and that also exist in the new layout
                 * 2. Remove that temporary group from the group mapping so that it doesn't get cleared when we clear the layout
                 */
                tempGroup = this.createGroup();
                this._groups.delete(tempGroup.api.id);
                const newPanels = Object.keys(data.panels);
                for (const panel of this.panels) {
                    if (newPanels.includes(panel.api.id)) {
                        existingPanels.set(panel.api.id, panel);
                    }
                }
                this.movingLock(() => {
                    Array.from(existingPanels.values()).forEach((panel) => {
                        this.moveGroupOrPanel({
                            from: {
                                groupId: panel.api.group.api.id,
                                panelId: panel.api.id,
                            },
                            to: {
                                group: tempGroup,
                                position: 'center',
                            },
                            keepEmptyGroups: true,
                        });
                    });
                });
            }
            this.clear();
            if (typeof data !== 'object' || data === null) {
                throw new Error('dockview: serialized layout must be a non-null object');
            }
            const { grid, panels, activeGroup } = data;
            if (grid.root.type !== 'branch' || !Array.isArray(grid.root.data)) {
                throw new Error('dockview: root must be of type branch');
            }
            try {
                // take note of the existing dimensions
                const width = this.width;
                const height = this.height;
                const createGroupFromSerializedState = (data) => {
                    const { id, locked, hideHeader, headerPosition, views, activeView, } = data;
                    if (typeof id !== 'string') {
                        throw new Error('dockview: group id must be of type string');
                    }
                    const group = this.createGroup({
                        id,
                        locked: !!locked,
                        hideHeader: !!hideHeader,
                        headerPosition,
                    });
                    this._onDidAddGroup.fire(group);
                    const createdPanels = [];
                    for (const child of views) {
                        /**
                         * Run the deserializer step seperately since this may fail to due corrupted external state.
                         * In running this section first we avoid firing lots of 'add' events in the event of a failure
                         * due to a corruption of input data.
                         */
                        const existingPanel = existingPanels.get(child);
                        if (tempGroup && existingPanel) {
                            this.movingLock(() => {
                                tempGroup.model.removePanel(existingPanel);
                            });
                            createdPanels.push(existingPanel);
                            existingPanel.updateFromStateModel(panels[child]);
                        }
                        else {
                            const panel = this._deserializer.fromJSON(panels[child], group);
                            createdPanels.push(panel);
                        }
                    }
                    for (let i = 0; i < views.length; i++) {
                        const panel = createdPanels[i];
                        const isActive = typeof activeView === 'string' &&
                            activeView === panel.id;
                        const hasExisting = existingPanels.has(panel.api.id);
                        if (hasExisting) {
                            this.movingLock(() => {
                                group.model.openPanel(panel, {
                                    skipSetActive: !isActive,
                                    skipSetGroupActive: true,
                                });
                            });
                        }
                        else {
                            group.model.openPanel(panel, {
                                skipSetActive: !isActive,
                                skipSetGroupActive: true,
                            });
                        }
                    }
                    // Restore tab groups before activating a fallback panel so
                    // that collapsed groups can clear the active panel correctly.
                    if (data.tabGroups && data.tabGroups.length > 0) {
                        group.model.restoreTabGroups(data.tabGroups);
                    }
                    if (!group.activePanel && group.panels.length > 0) {
                        group.model.openPanel(group.panels[group.panels.length - 1], {
                            skipSetGroupActive: true,
                        });
                    }
                    return group;
                };
                this.gridview.deserialize(grid, {
                    fromJSON: (node) => {
                        return createGroupFromSerializedState(node.data);
                    },
                });
                this._layoutFromShell(width, height);
                if (data.edgeGroups) {
                    // Auto-create edge groups for positions in the serialized state
                    // that don't already have a group registered (e.g. when fromJSON
                    // is called before the user has called addEdgeGroup).
                    for (const _position of [
                        'top',
                        'bottom',
                        'left',
                        'right',
                    ]) {
                        const fixedData = data.edgeGroups[_position];
                        if (fixedData && !this._edgeGroups.has(_position)) {
                            const groupState = fixedData.group;
                            const id = (_a = groupState === null || groupState === void 0 ? void 0 : groupState.id) !== null && _a !== void 0 ? _a : `${_position}-group`;
                            this.addEdgeGroup(_position, { id });
                        }
                    }
                    // Restore panel contents of edge groups
                    for (const [position, edgeGroup] of this._edgeGroups) {
                        const edgeData = data.edgeGroups[position];
                        const groupState = edgeData === null || edgeData === void 0 ? void 0 : edgeData.group;
                        if (groupState) {
                            const { views, activeView } = groupState;
                            const createdPanels = [];
                            for (const panelId of views) {
                                if (panels[panelId]) {
                                    const panel = this._deserializer.fromJSON(panels[panelId], edgeGroup);
                                    createdPanels.push(panel);
                                }
                            }
                            for (let i = 0; i < createdPanels.length; i++) {
                                const panel = createdPanels[i];
                                const isActive = activeView === panel.id;
                                edgeGroup.model.openPanel(panel, {
                                    skipSetActive: !isActive,
                                    skipSetGroupActive: true,
                                });
                            }
                            // Restore tab groups before activating a fallback panel
                            if (groupState.tabGroups &&
                                groupState.tabGroups.length > 0) {
                                edgeGroup.model.restoreTabGroups(groupState.tabGroups);
                            }
                            if (!edgeGroup.activePanel &&
                                edgeGroup.panels.length > 0) {
                                edgeGroup.model.openPanel(edgeGroup.panels[edgeGroup.panels.length - 1], { skipSetGroupActive: true });
                            }
                        }
                    }
                    this._shellManager.fromJSON(data.edgeGroups);
                }
                const serializedFloatingGroups = (_b = data.floatingGroups) !== null && _b !== void 0 ? _b : [];
                for (const serializedFloatingGroup of serializedFloatingGroups) {
                    const { data, position } = serializedFloatingGroup;
                    const group = createGroupFromSerializedState(data);
                    this.addFloatingGroup(group, {
                        position: position,
                        width: position.width,
                        height: position.height,
                        skipRemoveGroup: true,
                        inDragMode: false,
                    });
                }
                const serializedPopoutGroups = (_c = data.popoutGroups) !== null && _c !== void 0 ? _c : [];
                // Create a promise that resolves when all popout groups are created
                const popoutPromises = [];
                // Queue popup group creation with delays to avoid browser blocking
                serializedPopoutGroups.forEach((serializedPopoutGroup, index) => {
                    const { data, position, gridReferenceGroup, url } = serializedPopoutGroup;
                    const group = createGroupFromSerializedState(data);
                    // Add a small delay for each popup after the first to avoid browser popup blocking
                    const popoutPromise = new Promise((resolve) => {
                        const cleanup = () => {
                            this._popoutRestorationCleanups.delete(cleanup);
                            clearTimeout(handle);
                            // The group was registered in _groups synchronously
                            // but the timer that would parent it into the popout
                            // window never ran. Dispose the orphan here so the
                            // next clear() doesn't trip over an unparented
                            // element. See issue #1304.
                            if (!this.isDisposed &&
                                this._groups.has(group.id) &&
                                group.element.parentElement === null) {
                                for (const panel of [...group.panels]) {
                                    this.removePanel(panel, {
                                        removeEmptyGroup: false,
                                    });
                                }
                                group.dispose();
                                this._groups.delete(group.id);
                                this._onDidRemoveGroup.fire(group);
                            }
                            resolve();
                        };
                        const handle = setTimeout(() => {
                            this._popoutRestorationCleanups.delete(cleanup);
                            // Guard against the component being disposed before
                            // this timer fires. Under React StrictMode the
                            // component is mounted -> disposed -> remounted, and
                            // without this guard the first instance's queued
                            // restoration would open a second popout window.
                            // See issue #851.
                            if (this.isDisposed) {
                                resolve();
                                return;
                            }
                            this.addPopoutGroup(group, {
                                position: position !== null && position !== void 0 ? position : undefined,
                                overridePopoutGroup: gridReferenceGroup
                                    ? group
                                    : undefined,
                                referenceGroup: gridReferenceGroup
                                    ? this.getPanel(gridReferenceGroup)
                                    : undefined,
                                popoutUrl: url,
                            });
                            resolve();
                        }, index * DESERIALIZATION_POPOUT_DELAY_MS); // 100ms delay between each popup
                        this._popoutRestorationCleanups.add(cleanup);
                    });
                    popoutPromises.push(popoutPromise);
                });
                // Store the promise for tests to wait on
                this._popoutRestorationPromise = Promise.all(popoutPromises).then(() => void 0);
                for (const floatingGroup of this._floatingGroups) {
                    floatingGroup.overlay.setBounds();
                }
                if (typeof activeGroup === 'string') {
                    const panel = this.getPanel(activeGroup);
                    if (panel) {
                        this.doSetGroupAndPanelActive(panel);
                    }
                }
            }
            catch (err) {
                console.error('dockview: failed to deserialize layout. Reverting changes', err);
                /**
                 * Takes all the successfully created groups and remove all of their panels.
                 */
                for (const group of this.groups) {
                    for (const panel of group.panels) {
                        this.removePanel(panel, {
                            removeEmptyGroup: false,
                            skipDispose: false,
                        });
                    }
                }
                /**
                 * To remove a group we cannot call this.removeGroup(...) since this makes assumptions about
                 * the underlying HTMLElement existing in the Gridview.
                 */
                for (const group of this.groups) {
                    group.dispose();
                    this._groups.delete(group.id);
                    this._onDidRemoveGroup.fire(group);
                }
                // iterate over a reassigned array since original array will be modified
                for (const floatingGroup of [...this._floatingGroups]) {
                    floatingGroup.dispose();
                }
                // fires clean-up events and clears the underlying HTML gridview.
                this.clear();
                /**
                 * even though we have cleaned-up we still want to inform the caller of their error
                 * and we'll do this through re-throwing the original error since afterall you would
                 * expect trying to load a corrupted layout to result in an error and not silently fail...
                 */
                throw err;
            }
            this.updateWatermark();
            // Force position updates for always visible panels after DOM layout is complete
            this.debouncedUpdateAllPositions();
            this._onDidLayoutFromJSON.fire();
        }
        clear() {
            const groups = Array.from(this._groups.values()).map((_) => _.value);
            const hasActiveGroup = !!this.activeGroup;
            for (const group of groups) {
                if ([...this._edgeGroups.values()].includes(group)) {
                    // Edge groups are structural - only clear their panels, not the group itself
                    const panels = [...group.panels];
                    for (const panel of panels) {
                        this.removePanel(panel, { removeEmptyGroup: false });
                    }
                    continue;
                }
                // remove the group will automatically remove the panels
                this.removeGroup(group, { skipActive: true });
            }
            if (hasActiveGroup) {
                this.doSetGroupAndPanelActive(undefined);
            }
            this.gridview.clear();
        }
        closeAllGroups() {
            for (const entry of this._groups.entries()) {
                const [_, group] = entry;
                group.value.model.closeAllPanels();
            }
        }
        addPanel(options) {
            var _a, _b;
            if (this.panels.find((_) => _.id === options.id)) {
                throw new Error(`dockview: panel with id ${options.id} already exists`);
            }
            let referenceGroup;
            if (options.position && options.floating) {
                throw new Error('dockview: you can only provide one of: position, floating as arguments to .addPanel(...)');
            }
            const initial = {
                width: options.initialWidth,
                height: options.initialHeight,
            };
            let index;
            if (options.position) {
                if (isPanelOptionsWithPanel(options.position)) {
                    const referencePanel = typeof options.position.referencePanel === 'string'
                        ? this.getGroupPanel(options.position.referencePanel)
                        : options.position.referencePanel;
                    index = options.position.index;
                    if (!referencePanel) {
                        throw new Error(`dockview: referencePanel '${options.position.referencePanel}' does not exist`);
                    }
                    referenceGroup = this.findGroup(referencePanel);
                }
                else if (isPanelOptionsWithGroup(options.position)) {
                    referenceGroup =
                        typeof options.position.referenceGroup === 'string'
                            ? (_a = this._groups.get(options.position.referenceGroup)) === null || _a === void 0 ? void 0 : _a.value
                            : options.position.referenceGroup;
                    index = options.position.index;
                    if (!referenceGroup) {
                        throw new Error(`dockview: referenceGroup '${options.position.referenceGroup}' does not exist`);
                    }
                }
                else {
                    const group = this.orthogonalize(directionToPosition(options.position.direction));
                    const panel = this.createPanel(options, group);
                    group.model.openPanel(panel, {
                        skipSetActive: options.inactive,
                        skipSetGroupActive: options.inactive,
                        index,
                    });
                    if (!options.inactive) {
                        this.doSetGroupAndPanelActive(group);
                    }
                    group.api.setSize({
                        height: initial === null || initial === void 0 ? void 0 : initial.height,
                        width: initial === null || initial === void 0 ? void 0 : initial.width,
                    });
                    return panel;
                }
            }
            else {
                referenceGroup = this.activeGroup;
            }
            let panel;
            if (referenceGroup) {
                const target = toTarget(((_b = options.position) === null || _b === void 0 ? void 0 : _b.direction) || 'within');
                if (options.floating) {
                    const group = this.createGroup();
                    this._onDidAddGroup.fire(group);
                    const floatingGroupOptions = typeof options.floating === 'object' &&
                        options.floating !== null
                        ? options.floating
                        : {};
                    this.addFloatingGroup(group, Object.assign(Object.assign({}, floatingGroupOptions), { inDragMode: false, skipRemoveGroup: true, skipActiveGroup: true }));
                    panel = this.createPanel(options, group);
                    group.model.openPanel(panel, {
                        skipSetActive: options.inactive,
                        skipSetGroupActive: options.inactive,
                        index,
                    });
                }
                else if (referenceGroup.api.location.type === 'floating' ||
                    referenceGroup.api.location.type === 'edge' ||
                    target === 'center') {
                    panel = this.createPanel(options, referenceGroup);
                    referenceGroup.model.openPanel(panel, {
                        skipSetActive: options.inactive,
                        skipSetGroupActive: options.inactive,
                        index,
                    });
                    referenceGroup.api.setSize({
                        width: initial === null || initial === void 0 ? void 0 : initial.width,
                        height: initial === null || initial === void 0 ? void 0 : initial.height,
                    });
                    if (!options.inactive) {
                        this.doSetGroupAndPanelActive(referenceGroup);
                    }
                }
                else {
                    const location = getGridLocation(referenceGroup.element);
                    const relativeLocation = getRelativeLocation(this.gridview.orientation, location, target);
                    const group = this.createGroupAtLocation(relativeLocation, this.orientationAtLocation(relativeLocation) ===
                        exports.Orientation.VERTICAL
                        ? initial === null || initial === void 0 ? void 0 : initial.height
                        : initial === null || initial === void 0 ? void 0 : initial.width);
                    panel = this.createPanel(options, group);
                    group.model.openPanel(panel, {
                        skipSetActive: options.inactive,
                        skipSetGroupActive: options.inactive,
                        index,
                    });
                    if (!options.inactive) {
                        this.doSetGroupAndPanelActive(group);
                    }
                }
            }
            else if (options.floating) {
                const group = this.createGroup();
                this._onDidAddGroup.fire(group);
                const coordinates = typeof options.floating === 'object' &&
                    options.floating !== null
                    ? options.floating
                    : {};
                this.addFloatingGroup(group, Object.assign(Object.assign({}, coordinates), { inDragMode: false, skipRemoveGroup: true, skipActiveGroup: true }));
                panel = this.createPanel(options, group);
                group.model.openPanel(panel, {
                    skipSetActive: options.inactive,
                    skipSetGroupActive: options.inactive,
                    index,
                });
            }
            else {
                const group = this.createGroupAtLocation([0], this.gridview.orientation === exports.Orientation.VERTICAL
                    ? initial === null || initial === void 0 ? void 0 : initial.height
                    : initial === null || initial === void 0 ? void 0 : initial.width);
                panel = this.createPanel(options, group);
                group.model.openPanel(panel, {
                    skipSetActive: options.inactive,
                    skipSetGroupActive: options.inactive,
                    index,
                });
                if (!options.inactive) {
                    this.doSetGroupAndPanelActive(group);
                }
            }
            return panel;
        }
        removePanel(panel, options = {
            removeEmptyGroup: true,
        }) {
            const group = panel.group;
            if (!group) {
                throw new Error(`dockview: cannot remove panel ${panel.id}. it's missing a group.`);
            }
            group.model.removePanel(panel, {
                skipSetActiveGroup: options.skipSetActiveGroup,
            });
            if (!options.skipDispose) {
                panel.group.model.renderContainer.detatch(panel);
                panel.dispose();
            }
            if (group.size === 0 && options.removeEmptyGroup) {
                this.removeGroup(group, { skipActive: options.skipSetActiveGroup });
            }
        }
        createWatermarkComponent() {
            if (this.options.createWatermarkComponent) {
                return this.options.createWatermarkComponent();
            }
            return new Watermark();
        }
        updateWatermark() {
            var _a, _b;
            if (this.groups.filter((x) => x.api.location.type === 'grid' && x.api.isVisible).length === 0) {
                if (!this._watermark) {
                    this._watermark = this.createWatermarkComponent();
                    this._watermark.init({
                        containerApi: new DockviewApi(this),
                    });
                    const watermarkContainer = document.createElement('div');
                    watermarkContainer.className = 'dv-watermark-container';
                    addTestId(watermarkContainer, 'watermark-component');
                    watermarkContainer.appendChild(this._watermark.element);
                    this.gridview.element.appendChild(watermarkContainer);
                }
            }
            else if (this._watermark) {
                this._watermark.element.parentElement.remove();
                (_b = (_a = this._watermark).dispose) === null || _b === void 0 ? void 0 : _b.call(_a);
                this._watermark = null;
            }
        }
        addGroup(options) {
            var _a;
            if (options) {
                let referenceGroup;
                if (isGroupOptionsWithPanel(options)) {
                    const referencePanel = typeof options.referencePanel === 'string'
                        ? this.panels.find((panel) => panel.id === options.referencePanel)
                        : options.referencePanel;
                    if (!referencePanel) {
                        throw new Error(`dockview: reference panel ${options.referencePanel} does not exist`);
                    }
                    referenceGroup = this.findGroup(referencePanel);
                    if (!referenceGroup) {
                        throw new Error(`dockview: reference group for reference panel ${options.referencePanel} does not exist`);
                    }
                }
                else if (isGroupOptionsWithGroup(options)) {
                    referenceGroup =
                        typeof options.referenceGroup === 'string'
                            ? (_a = this._groups.get(options.referenceGroup)) === null || _a === void 0 ? void 0 : _a.value
                            : options.referenceGroup;
                    if (!referenceGroup) {
                        throw new Error(`dockview: reference group ${options.referenceGroup} does not exist`);
                    }
                }
                else {
                    const group = this.orthogonalize(directionToPosition(options.direction), options);
                    if (!options.skipSetActive) {
                        this.doSetGroupAndPanelActive(group);
                    }
                    return group;
                }
                const target = toTarget(options.direction || 'within');
                const location = getGridLocation(referenceGroup.element);
                const relativeLocation = getRelativeLocation(this.gridview.orientation, location, target);
                const group = this.createGroup(options);
                const size = this.getLocationOrientation(relativeLocation) ===
                    exports.Orientation.VERTICAL
                    ? options.initialHeight
                    : options.initialWidth;
                this.doAddGroup(group, relativeLocation, size);
                if (!options.skipSetActive) {
                    this.doSetGroupAndPanelActive(group);
                }
                return group;
            }
            else {
                const group = this.createGroup(options);
                this.doAddGroup(group);
                this.doSetGroupAndPanelActive(group);
                return group;
            }
        }
        getLocationOrientation(location) {
            return location.length % 2 == 0 &&
                this.gridview.orientation === exports.Orientation.HORIZONTAL
                ? exports.Orientation.HORIZONTAL
                : exports.Orientation.VERTICAL;
        }
        removeGroup(group, options) {
            this.doRemoveGroup(group, options);
        }
        doRemoveGroup(group, options) {
            var _a;
            // Edge groups are permanent structural elements - never remove them from the layout
            if ([...this._edgeGroups.values()].includes(group)) {
                return group;
            }
            const panels = [...group.panels]; // reassign since group panels will mutate
            if (!(options === null || options === void 0 ? void 0 : options.skipDispose)) {
                for (const panel of panels) {
                    this.removePanel(panel, {
                        removeEmptyGroup: false,
                        skipDispose: (_a = options === null || options === void 0 ? void 0 : options.skipDispose) !== null && _a !== void 0 ? _a : false,
                    });
                }
            }
            const activePanel = this.activePanel;
            if (group.api.location.type === 'floating') {
                const floatingGroup = this._floatingGroups.find((_) => _.group === group);
                if (floatingGroup) {
                    if (!(options === null || options === void 0 ? void 0 : options.skipDispose)) {
                        floatingGroup.group.dispose();
                        this._groups.delete(group.id);
                        this._onDidRemoveGroup.fire(group);
                    }
                    remove(this._floatingGroups, floatingGroup);
                    floatingGroup.dispose();
                    if (!(options === null || options === void 0 ? void 0 : options.skipActive) && this._activeGroup === group) {
                        const groups = Array.from(this._groups.values());
                        this.doSetGroupAndPanelActive(groups.length > 0 ? groups[0].value : undefined);
                    }
                    return floatingGroup.group;
                }
                throw new Error('dockview: failed to find floating group');
            }
            if (group.api.location.type === 'popout') {
                const selectedGroup = this._popoutGroups.find((_) => _.popoutGroup === group);
                if (selectedGroup) {
                    if (!(options === null || options === void 0 ? void 0 : options.skipDispose)) {
                        if (!(options === null || options === void 0 ? void 0 : options.skipPopoutAssociated)) {
                            const refGroup = selectedGroup.referenceGroup
                                ? this.getPanel(selectedGroup.referenceGroup)
                                : undefined;
                            if (refGroup && refGroup.panels.length === 0) {
                                this.removeGroup(refGroup);
                            }
                        }
                        selectedGroup.popoutGroup.dispose();
                        this._groups.delete(group.id);
                        this._onDidRemoveGroup.fire(group);
                    }
                    remove(this._popoutGroups, selectedGroup);
                    const removedGroup = selectedGroup.disposable.dispose();
                    if (!(options === null || options === void 0 ? void 0 : options.skipPopoutReturn) && removedGroup) {
                        this.doAddGroup(removedGroup, [0]);
                        this.doSetGroupAndPanelActive(removedGroup);
                    }
                    if (!(options === null || options === void 0 ? void 0 : options.skipActive) && this._activeGroup === group) {
                        const groups = Array.from(this._groups.values());
                        this.doSetGroupAndPanelActive(groups.length > 0 ? groups[0].value : undefined);
                    }
                    this.updateWatermark();
                    return selectedGroup.popoutGroup;
                }
                throw new Error('dockview: failed to find popout group');
            }
            const re = super.doRemoveGroup(group, options);
            if (!(options === null || options === void 0 ? void 0 : options.skipActive)) {
                if (this.activePanel !== activePanel) {
                    this._onDidActivePanelChange.fire(this.activePanel);
                }
            }
            return re;
        }
        debouncedUpdateAllPositions() {
            if (this._updatePositionsFrameId !== undefined) {
                cancelAnimationFrame(this._updatePositionsFrameId);
            }
            this._updatePositionsFrameId = requestAnimationFrame(() => {
                this._updatePositionsFrameId = undefined;
                this.overlayRenderContainer.updateAllPositions();
            });
        }
        movingLock(func) {
            const isMoving = this._moving;
            try {
                this._moving = true;
                return func();
            }
            finally {
                this._moving = isMoving;
            }
        }
        moveGroupOrPanel(options) {
            var _a;
            const destinationGroup = options.to.group;
            const sourceGroupId = options.from.groupId;
            const sourceItemId = options.from.panelId;
            const destinationTarget = options.to.position;
            const destinationIndex = options.to.index;
            const sourceGroup = sourceGroupId
                ? (_a = this._groups.get(sourceGroupId)) === null || _a === void 0 ? void 0 : _a.value
                : undefined;
            if (!sourceGroup) {
                throw new Error(`dockview: Failed to find group id ${sourceGroupId}`);
            }
            if (sourceItemId === undefined) {
                if (options.from.tabGroupId) {
                    /**
                     * Moving a tab group (subset of panels) into another group
                     */
                    this.moveTabGroupToGroup({
                        sourceGroup,
                        tabGroupId: options.from.tabGroupId,
                        destinationGroup,
                        destinationTarget,
                        destinationIndex,
                        skipSetActive: options.skipSetActive,
                        keepEmptyGroups: options.keepEmptyGroups,
                    });
                }
                else {
                    /**
                     * Moving an entire group into another group
                     */
                    this.moveGroup({
                        from: { group: sourceGroup },
                        to: {
                            group: destinationGroup,
                            position: destinationTarget,
                        },
                        skipSetActive: options.skipSetActive,
                    });
                }
                return;
            }
            if (!destinationTarget || destinationTarget === 'center') {
                /**
                 * Dropping a panel within another group
                 */
                const removedPanel = this.movingLock(() => sourceGroup.model.removePanel(sourceItemId, {
                    skipSetActive: false,
                    skipSetActiveGroup: true,
                }));
                if (!removedPanel) {
                    throw new Error(`dockview: No panel with id ${sourceItemId}`);
                }
                if (!options.keepEmptyGroups && sourceGroup.model.size === 0) {
                    // remove the group and do not set a new group as active
                    this.doRemoveGroup(sourceGroup, { skipActive: true });
                }
                // Check if destination group is empty - if so, force render the component
                const isDestinationGroupEmpty = destinationGroup.model.size === 0;
                this.movingLock(() => {
                    var _a;
                    return destinationGroup.model.openPanel(removedPanel, {
                        index: destinationIndex,
                        skipSetActive: ((_a = options.skipSetActive) !== null && _a !== void 0 ? _a : false) &&
                            !isDestinationGroupEmpty,
                        skipSetGroupActive: true,
                    });
                });
                if (!options.skipSetActive) {
                    this.doSetGroupAndPanelActive(destinationGroup);
                }
                this._onDidMovePanel.fire({
                    panel: removedPanel,
                    from: sourceGroup,
                });
            }
            else {
                /**
                 * Dropping a panel to the extremities of a group which will place that panel
                 * into an adjacent group
                 */
                const referenceLocation = getGridLocation(destinationGroup.element);
                const targetLocation = getRelativeLocation(this.gridview.orientation, referenceLocation, destinationTarget);
                if (sourceGroup.size < 2) {
                    /**
                     * If we are moving from a group which only has one panel left we will consider
                     * moving the group itself rather than moving the panel into a newly created group
                     */
                    const [targetParentLocation, to] = tail(targetLocation);
                    if (sourceGroup.api.location.type === 'grid') {
                        const sourceLocation = getGridLocation(sourceGroup.element);
                        const [sourceParentLocation, from] = tail(sourceLocation);
                        if (sequenceEquals(sourceParentLocation, targetParentLocation)) {
                            // special case when 'swapping' two views within same grid location
                            // if a group has one tab - we are essentially moving the 'group'
                            // which is equivalent to swapping two views in this case
                            this.gridview.moveView(sourceParentLocation, from, to);
                            this._onDidMovePanel.fire({
                                panel: this.getGroupPanel(sourceItemId),
                                from: sourceGroup,
                            });
                            return;
                        }
                    }
                    if (sourceGroup.api.location.type === 'popout') {
                        /**
                         * the source group is a popout group with a single panel
                         *
                         * 1. remove the panel from the group without triggering any events
                         * 2. remove the popout group — this may cascade-remove the empty
                         *    reference group it left behind in the main grid (see
                         *    doRemoveGroup for popout groups), which can shift grid indices
                         * 3. recompute the target location now that the grid is stable
                         * 4. create a new group at the recomputed location and add that panel
                         */
                        const popoutGroup = this._popoutGroups.find((group) => group.popoutGroup === sourceGroup);
                        const removedPanel = this.movingLock(() => popoutGroup.popoutGroup.model.removePanel(popoutGroup.popoutGroup.panels[0], {
                            skipSetActive: true,
                            skipSetActiveGroup: true,
                        }));
                        this.doRemoveGroup(sourceGroup, { skipActive: true });
                        const updatedTargetLocation = getRelativeLocation(this.gridview.orientation, getGridLocation(destinationGroup.element), destinationTarget);
                        const newGroup = this.createGroupAtLocation(updatedTargetLocation);
                        this.movingLock(() => newGroup.model.openPanel(removedPanel, {
                            skipSetActive: true,
                        }));
                        this.doSetGroupAndPanelActive(newGroup);
                        this._onDidMovePanel.fire({
                            panel: this.getGroupPanel(sourceItemId),
                            from: sourceGroup,
                        });
                        return;
                    }
                    if (sourceGroup.api.location.type === 'edge') {
                        /**
                         * Edge groups are permanent structural elements — never move the
                         * group itself. Instead extract the panel and create a new grid group,
                         * leaving the edge slot intact (same behaviour as the size >= 2 path).
                         */
                        const removedPanel = this.movingLock(() => sourceGroup.model.removePanel(sourceItemId, {
                            skipSetActive: false,
                            skipSetActiveGroup: true,
                        }));
                        if (!removedPanel) {
                            throw new Error(`dockview: No panel with id ${sourceItemId}`);
                        }
                        const newGroup = this.createGroupAtLocation(targetLocation);
                        this.movingLock(() => newGroup.model.openPanel(removedPanel, {
                            skipSetGroupActive: true,
                        }));
                        this.doSetGroupAndPanelActive(newGroup);
                        this._onDidMovePanel.fire({
                            panel: removedPanel,
                            from: sourceGroup,
                        });
                        return;
                    }
                    // source group will become empty so delete the group
                    const targetGroup = this.movingLock(() => this.doRemoveGroup(sourceGroup, {
                        skipActive: true,
                        skipDispose: true,
                    }));
                    // after deleting the group we need to re-evaulate the ref location
                    const updatedReferenceLocation = getGridLocation(destinationGroup.element);
                    const location = getRelativeLocation(this.gridview.orientation, updatedReferenceLocation, destinationTarget);
                    this.movingLock(() => this.doAddGroup(targetGroup, location));
                    this.doSetGroupAndPanelActive(targetGroup);
                    this._onDidMovePanel.fire({
                        panel: this.getGroupPanel(sourceItemId),
                        from: sourceGroup,
                    });
                }
                else {
                    /**
                     * The group we are removing from has many panels, we need to remove the panels we are moving,
                     * create a new group, add the panels to that new group and add the new group in an appropiate position
                     */
                    const removedPanel = this.movingLock(() => sourceGroup.model.removePanel(sourceItemId, {
                        skipSetActive: false,
                        skipSetActiveGroup: true,
                    }));
                    if (!removedPanel) {
                        throw new Error(`dockview: No panel with id ${sourceItemId}`);
                    }
                    const dropLocation = getRelativeLocation(this.gridview.orientation, referenceLocation, destinationTarget);
                    const group = this.createGroupAtLocation(dropLocation);
                    this.movingLock(() => group.model.openPanel(removedPanel, {
                        skipSetGroupActive: true,
                    }));
                    this.doSetGroupAndPanelActive(group);
                    this._onDidMovePanel.fire({
                        panel: removedPanel,
                        from: sourceGroup,
                    });
                }
            }
        }
        moveTabGroupToGroup(options) {
            const { sourceGroup, tabGroupId, destinationGroup, destinationTarget, destinationIndex, } = options;
            const tabGroup = sourceGroup.model
                .getTabGroups()
                .find((tg) => tg.id === tabGroupId);
            if (!tabGroup || tabGroup.panelIds.length === 0) {
                return;
            }
            // Snapshot tab group metadata before removing panels
            const label = tabGroup.label;
            const color = tabGroup.color;
            const collapsed = tabGroup.collapsed;
            const componentParams = tabGroup.componentParams;
            const panelIds = [...tabGroup.panelIds];
            // Capture the destination's grid location BEFORE potentially
            // removing the source group, in case source === destination and
            // the source becomes empty after panel removal.
            const referenceLocation = destinationTarget && destinationTarget !== 'center'
                ? getGridLocation(destinationGroup.element)
                : undefined;
            // Remove panels from the source group
            const removedPanels = this.movingLock(() => panelIds
                .map((pid) => sourceGroup.model.removePanel(pid, {
                skipSetActive: false,
                skipSetActiveGroup: true,
            }))
                .filter((p) => p !== undefined));
            if (removedPanels.length === 0) {
                return;
            }
            const addPanelsToGroup = (targetGroup) => {
                this.movingLock(() => {
                    for (const panel of removedPanels) {
                        targetGroup.model.openPanel(panel, {
                            index: destinationIndex,
                            skipSetActive: true,
                            skipSetGroupActive: true,
                        });
                    }
                });
                // Recreate the tab group in the destination
                const newTabGroup = targetGroup.model.createTabGroup({
                    label,
                    color,
                    collapsed,
                    componentParams,
                });
                for (const panel of removedPanels) {
                    targetGroup.model.addPanelToTabGroup(newTabGroup.id, panel.id);
                }
                if (!options.skipSetActive) {
                    this.doSetGroupAndPanelActive(targetGroup);
                }
                for (const panel of removedPanels) {
                    this._onDidMovePanel.fire({
                        panel,
                        from: sourceGroup,
                    });
                }
            };
            let targetGroup;
            if (!destinationTarget ||
                destinationTarget === 'center' ||
                !referenceLocation) {
                targetGroup = destinationGroup;
            }
            else {
                const dropLocation = getRelativeLocation(this.gridview.orientation, referenceLocation, destinationTarget);
                targetGroup = this.createGroupAtLocation(dropLocation);
            }
            // Remove the source group if it became empty. We compare against
            // the actual targetGroup (which is a freshly-created group for
            // edge drops) rather than the originally-passed destinationGroup,
            // so a tab-group drag onto its own group's edge still cleans up
            // the now-empty source.
            if (!options.keepEmptyGroups &&
                sourceGroup.model.size === 0 &&
                sourceGroup !== targetGroup) {
                this.doRemoveGroup(sourceGroup, { skipActive: true });
            }
            addPanelsToGroup(targetGroup);
        }
        moveGroup(options) {
            const from = options.from.group;
            const to = options.to.group;
            const target = options.to.position;
            // The group whose panels end up at the target. For non-edge moves
            // we relocate `from` itself; for edge moves we move panels into a
            // freshly created group so the edge slot stays anchored.
            let source = from;
            if (target === 'center') {
                const activePanel = from.activePanel;
                // Snapshot tab group metadata before removing panels so we
                // can recreate the tab groups in the destination after the
                // panels are merged in.
                const tabGroupSnapshots = from.model.getTabGroups().map((tg) => ({
                    label: tg.label,
                    color: tg.color,
                    collapsed: tg.collapsed,
                    componentParams: tg.componentParams,
                    panelIds: [...tg.panelIds],
                }));
                const panels = this.movingLock(() => [...from.panels].map((p) => from.model.removePanel(p.id, {
                    skipSetActive: true,
                })));
                if ((from === null || from === void 0 ? void 0 : from.model.size) === 0) {
                    this.doRemoveGroup(from, { skipActive: true });
                }
                this.movingLock(() => {
                    for (const panel of panels) {
                        to.model.openPanel(panel, {
                            skipSetActive: panel !== activePanel,
                            skipSetGroupActive: true,
                        });
                    }
                });
                for (const snapshot of tabGroupSnapshots) {
                    const newTabGroup = to.model.createTabGroup({
                        label: snapshot.label,
                        color: snapshot.color,
                        collapsed: snapshot.collapsed,
                        componentParams: snapshot.componentParams,
                    });
                    for (const panelId of snapshot.panelIds) {
                        to.model.addPanelToTabGroup(newTabGroup.id, panelId);
                    }
                }
                // Ensure group becomes active after move
                if (options.skipSetActive !== true) {
                    // For center moves (merges), we need to ensure the target group is active
                    // unless explicitly told not to (skipSetActive: true)
                    this.doSetGroupAndPanelActive(to);
                }
                else if (!this.activePanel) {
                    // Even with skipSetActive: true, ensure there's an active panel if none exists
                    // This maintains basic functionality while respecting skipSetActive
                    this.doSetGroupAndPanelActive(to);
                }
            }
            else {
                if (from.api.location.type === 'edge') {
                    /**
                     * Edge groups are permanent structural elements and must
                     * stay anchored in their edge slot. Move the panels into a
                     * new group; the auto-collapse listener registered in
                     * addEdgeGroup will collapse the now-empty edge slot once
                     * the last panel leaves. The placement code below then
                     * positions `source` like any other moved group.
                     */
                    const activePanel = from.activePanel;
                    // Snapshot tab group metadata so the new group inherits
                    // the tab grouping from the edge slot.
                    const tabGroupSnapshots = from.model
                        .getTabGroups()
                        .map((tg) => ({
                        label: tg.label,
                        color: tg.color,
                        collapsed: tg.collapsed,
                        componentParams: tg.componentParams,
                        panelIds: [...tg.panelIds],
                    }));
                    const movedPanels = this.movingLock(() => [...from.panels].map((p) => from.model.removePanel(p.id, { skipSetActive: true })));
                    source = this.createGroup();
                    this.movingLock(() => {
                        for (const panel of movedPanels) {
                            source.model.openPanel(panel, {
                                skipSetActive: panel !== activePanel,
                                skipSetGroupActive: true,
                            });
                        }
                    });
                    for (const snapshot of tabGroupSnapshots) {
                        const newTabGroup = source.model.createTabGroup({
                            label: snapshot.label,
                            color: snapshot.color,
                            collapsed: snapshot.collapsed,
                            componentParams: snapshot.componentParams,
                        });
                        for (const panelId of snapshot.panelIds) {
                            source.model.addPanelToTabGroup(newTabGroup.id, panelId);
                        }
                    }
                }
                else {
                    switch (from.api.location.type) {
                        case 'grid':
                            this.gridview.removeView(getGridLocation(from.element));
                            break;
                        case 'floating': {
                            const selectedFloatingGroup = this._floatingGroups.find((x) => x.group === from);
                            if (!selectedFloatingGroup) {
                                throw new Error('dockview: failed to find floating group');
                            }
                            selectedFloatingGroup.dispose();
                            break;
                        }
                        case 'popout': {
                            const selectedPopoutGroup = this._popoutGroups.find((x) => x.popoutGroup === from);
                            if (!selectedPopoutGroup) {
                                throw new Error('dockview: failed to find popout group');
                            }
                            // Remove from popout groups list to prevent automatic restoration
                            const index = this._popoutGroups.indexOf(selectedPopoutGroup);
                            if (index >= 0) {
                                this._popoutGroups.splice(index, 1);
                            }
                            // Clean up the reference group (ghost) if it exists and is hidden
                            if (selectedPopoutGroup.referenceGroup) {
                                const referenceGroup = this.getPanel(selectedPopoutGroup.referenceGroup);
                                if (referenceGroup &&
                                    !referenceGroup.api.isVisible) {
                                    this.doRemoveGroup(referenceGroup, {
                                        skipActive: true,
                                    });
                                }
                            }
                            // Manually dispose the window without triggering restoration
                            selectedPopoutGroup.window.dispose();
                            // Update group's location and containers for target
                            if (to.api.location.type === 'grid') {
                                from.model.renderContainer =
                                    this.overlayRenderContainer;
                                from.model.dropTargetContainer =
                                    this.rootDropTargetContainer;
                                from.model.location = { type: 'grid' };
                            }
                            else if (to.api.location.type === 'floating') {
                                from.model.renderContainer =
                                    this.overlayRenderContainer;
                                from.model.dropTargetContainer =
                                    this.rootDropTargetContainer;
                                from.model.location = { type: 'floating' };
                            }
                            break;
                        }
                    }
                }
                // For moves to grid locations
                if (to.api.location.type === 'grid') {
                    const referenceLocation = getGridLocation(to.element);
                    const dropLocation = getRelativeLocation(this.gridview.orientation, referenceLocation, target);
                    // Add to grid for all moves targeting grid location
                    let size;
                    switch (this.gridview.orientation) {
                        case exports.Orientation.VERTICAL:
                            size =
                                referenceLocation.length % 2 == 0
                                    ? from.api.width
                                    : from.api.height;
                            break;
                        case exports.Orientation.HORIZONTAL:
                            size =
                                referenceLocation.length % 2 == 0
                                    ? from.api.height
                                    : from.api.width;
                            break;
                    }
                    this.gridview.addView(source, size, dropLocation);
                }
                else if (to.api.location.type === 'floating') {
                    // For moves to floating locations, add as floating group
                    // Get the position/size from the target floating group
                    const targetFloatingGroup = this._floatingGroups.find((x) => x.group === to);
                    if (targetFloatingGroup) {
                        const box = targetFloatingGroup.overlay.toJSON();
                        // Calculate position based on available properties
                        let left, top;
                        if ('left' in box) {
                            left = box.left + 50;
                        }
                        else if ('right' in box) {
                            left = Math.max(0, box.right - box.width - 50);
                        }
                        else {
                            left = 50; // Default fallback
                        }
                        if ('top' in box) {
                            top = box.top + 50;
                        }
                        else if ('bottom' in box) {
                            top = Math.max(0, box.bottom - box.height - 50);
                        }
                        else {
                            top = 50; // Default fallback
                        }
                        this.addFloatingGroup(source, {
                            height: box.height,
                            width: box.width,
                            position: {
                                left,
                                top,
                            },
                        });
                    }
                }
            }
            source.panels.forEach((panel) => {
                this._onDidMovePanel.fire({ panel, from });
            });
            this.debouncedUpdateAllPositions();
            // Ensure group becomes active after move
            if (options.skipSetActive === false) {
                // Only activate when explicitly requested (skipSetActive: false)
                // Use 'to' group for non-center moves since 'from' may have been destroyed
                const targetGroup = to !== null && to !== void 0 ? to : from;
                this.doSetGroupAndPanelActive(targetGroup);
            }
            else if (source !== from && options.skipSetActive !== true) {
                // Edge group moves create a fresh `source` group; activate it
                // by default so the moved panels receive focus.
                this.doSetGroupAndPanelActive(source);
            }
        }
        doSetGroupActive(group) {
            super.doSetGroupActive(group);
            const activePanel = this.activePanel;
            if (!this._moving &&
                activePanel !== this._onDidActivePanelChange.value) {
                this._onDidActivePanelChange.fire(activePanel);
            }
        }
        doSetGroupAndPanelActive(group) {
            super.doSetGroupActive(group);
            const activePanel = this.activePanel;
            if (group &&
                this.hasMaximizedGroup() &&
                !this.isMaximizedGroup(group)) {
                this.exitMaximizedGroup();
            }
            if (!this._moving &&
                activePanel !== this._onDidActivePanelChange.value) {
                this._onDidActivePanelChange.fire(activePanel);
            }
        }
        getNextGroupId() {
            let id = this.nextGroupId.next();
            while (this._groups.has(id)) {
                id = this.nextGroupId.next();
            }
            return id;
        }
        createGroup(options) {
            if (!options) {
                options = {};
            }
            let id = options === null || options === void 0 ? void 0 : options.id;
            if (id && this._groups.has(options.id)) {
                console.warn(`dockview: Duplicate group id ${options === null || options === void 0 ? void 0 : options.id}. reassigning group id to avoid errors`);
                id = undefined;
            }
            if (!id) {
                id = this.nextGroupId.next();
                while (this._groups.has(id)) {
                    id = this.nextGroupId.next();
                }
            }
            const view = new DockviewGroupPanel(this, id, options);
            view.init({ params: {}, accessor: this });
            if (!this._groups.has(view.id)) {
                const disposable = new CompositeDisposable(view.model.onTabDragStart((event) => {
                    this._onWillDragPanel.fire(event);
                }), view.model.onGroupDragStart((event) => {
                    this._onWillDragGroup.fire(event);
                }), view.model.onMove((event) => {
                    const { groupId, itemId, target, index, tabGroupId } = event;
                    this.moveGroupOrPanel({
                        from: {
                            groupId: groupId,
                            panelId: itemId,
                            tabGroupId,
                        },
                        to: {
                            group: view,
                            position: target,
                            index,
                        },
                    });
                }), view.model.onDidDrop((event) => {
                    this._onDidDrop.fire(event);
                }), view.model.onWillDrop((event) => {
                    this._onWillDrop.fire(event);
                }), view.model.onWillShowOverlay((event) => {
                    if (this.options.disableDnd) {
                        event.preventDefault();
                        return;
                    }
                    this._onWillShowOverlay.fire(event);
                }), view.model.onUnhandledDragOverEvent((event) => {
                    this._onUnhandledDragOverEvent.fire(event);
                }), view.model.onDidAddPanel((event) => {
                    if (this._moving) {
                        return;
                    }
                    this._onDidAddPanel.fire(event.panel);
                }), view.model.onDidRemovePanel((event) => {
                    if (this._moving) {
                        return;
                    }
                    this._onDidRemovePanel.fire(event.panel);
                }), view.model.onDidActivePanelChange((event) => {
                    if (this._moving) {
                        return;
                    }
                    if (event.panel !== this.activePanel) {
                        return;
                    }
                    if (this._onDidActivePanelChange.value !== event.panel) {
                        this._onDidActivePanelChange.fire(event.panel);
                    }
                }), view.model.onDidCreateTabGroup((e) => {
                    this._onDidCreateTabGroup.fire(e);
                }), view.model.onDidDestroyTabGroup((e) => {
                    this._onDidDestroyTabGroup.fire(e);
                }), view.model.onDidAddPanelToTabGroup((e) => {
                    this._onDidAddPanelToTabGroup.fire(e);
                }), view.model.onDidRemovePanelFromTabGroup((e) => {
                    this._onDidRemovePanelFromTabGroup.fire(e);
                }), view.model.onDidTabGroupChange((e) => {
                    this._onDidTabGroupChange.fire(e);
                }), view.model.onDidTabGroupCollapsedChange((e) => {
                    this._onDidTabGroupCollapsedChange.fire(e);
                }), exports.DockviewEvent.any(view.model.onDidPanelTitleChange, view.model.onDidPanelParametersChange)(() => {
                    this._bufferOnDidLayoutChange.fire();
                }));
                this._groups.set(view.id, { value: view, disposable });
            }
            // TODO: must be called after the above listeners have been setup, not an ideal pattern
            view.initialize();
            return view;
        }
        createPanel(options, group) {
            var _a, _b, _c;
            const contentComponent = options.component;
            const tabComponent = (_a = options.tabComponent) !== null && _a !== void 0 ? _a : this.options.defaultTabComponent;
            const view = new DockviewPanelModel(this, options.id, contentComponent, tabComponent);
            const panel = new DockviewPanel(options.id, contentComponent, tabComponent, this, this._api, group, view, {
                renderer: options.renderer,
                minimumWidth: options.minimumWidth,
                minimumHeight: options.minimumHeight,
                maximumWidth: options.maximumWidth,
                maximumHeight: options.maximumHeight,
            });
            panel.init({
                title: (_b = options.title) !== null && _b !== void 0 ? _b : options.id,
                params: (_c = options === null || options === void 0 ? void 0 : options.params) !== null && _c !== void 0 ? _c : {},
            });
            return panel;
        }
        createGroupAtLocation(location, size, options) {
            const group = this.createGroup(options);
            this.doAddGroup(group, location, size);
            return group;
        }
        findGroup(panel) {
            var _a;
            return (_a = Array.from(this._groups.values()).find((group) => group.value.model.containsPanel(panel))) === null || _a === void 0 ? void 0 : _a.value;
        }
        orientationAtLocation(location) {
            const rootOrientation = this.gridview.orientation;
            return location.length % 2 == 1
                ? rootOrientation
                : orthogonal(rootOrientation);
        }
        updateDropTargetModel(options) {
            if ('dndEdges' in options) {
                const disabled = typeof options.dndEdges === 'boolean' &&
                    options.dndEdges === false;
                this._rootDropTarget.disabled = disabled;
                this._rootPointerDropTarget.disabled = disabled;
                if (typeof options.dndEdges === 'object' &&
                    options.dndEdges !== null) {
                    this._rootDropTarget.setOverlayModel(options.dndEdges);
                    this._rootPointerDropTarget.setOverlayModel(options.dndEdges);
                }
                else {
                    this._rootDropTarget.setOverlayModel(DEFAULT_ROOT_OVERLAY_MODEL);
                    this._rootPointerDropTarget.setOverlayModel(DEFAULT_ROOT_OVERLAY_MODEL);
                }
            }
            if ('rootOverlayModel' in options) {
                this.updateDropTargetModel({ dndEdges: options.dndEdges });
            }
        }
        updateTheme() {
            var _a, _b, _c, _d, _e, _f, _g, _h, _j;
            const theme = (_a = this._options.theme) !== null && _a !== void 0 ? _a : themeAbyss;
            // Apply the theme class only to the shell so edge groups and the
            // main grid both inherit its CSS custom properties via the cascade.
            // Re-declaring it on `.dv-dockview` would block consumer overrides
            // set on the shell from reaching the dockview subtree.
            (_b = this._shellThemeClassnames) === null || _b === void 0 ? void 0 : _b.setClassNames(theme.className);
            this.gridview.margin = (_c = theme.gap) !== null && _c !== void 0 ? _c : 0;
            (_d = this._shellManager) === null || _d === void 0 ? void 0 : _d.updateTheme((_e = theme.gap) !== null && _e !== void 0 ? _e : 0, (_f = theme.edgeGroupCollapsedSize) !== null && _f !== void 0 ? _f : 35);
            if (theme.dndOverlayBorder !== undefined) {
                this.element.style.setProperty('--dv-drag-over-border', theme.dndOverlayBorder);
                (_g = this._shellManager) === null || _g === void 0 ? void 0 : _g.element.style.setProperty('--dv-drag-over-border', theme.dndOverlayBorder);
            }
            else {
                this.element.style.removeProperty('--dv-drag-over-border');
                (_h = this._shellManager) === null || _h === void 0 ? void 0 : _h.element.style.removeProperty('--dv-drag-over-border');
            }
            switch (theme.dndOverlayMounting) {
                case 'absolute':
                    this.rootDropTargetContainer.disabled = false;
                    break;
                case 'relative':
                default:
                    this.rootDropTargetContainer.disabled = true;
                    break;
            }
            // Toggle a CSS class so theme stylesheets can scope pure-CSS
            // tab group indicator rules to the 'none' mode only.
            const indicatorNone = ((_j = theme.tabGroupIndicator) !== null && _j !== void 0 ? _j : 'wrap') === 'none';
            toggleClass(this.element, 'dv-tab-group-indicator-none', indicatorNone);
            if (this._shellManager) {
                toggleClass(this._shellManager.element, 'dv-tab-group-indicator-none', indicatorNone);
            }
            // Re-render tab group indicators so the new tabGroupIndicator mode takes effect
            for (const group of this.groups) {
                group.model.updateTabGroups();
            }
        }
    }

    class GridviewComponent extends BaseGrid {
        get orientation() {
            return this.gridview.orientation;
        }
        set orientation(value) {
            this.gridview.orientation = value;
        }
        get options() {
            return this._options;
        }
        get deserializer() {
            return this._deserializer;
        }
        set deserializer(value) {
            this._deserializer = value;
        }
        constructor(container, options) {
            var _a;
            super(container, {
                proportionalLayout: (_a = options.proportionalLayout) !== null && _a !== void 0 ? _a : true,
                orientation: options.orientation,
                styles: options.hideBorders
                    ? { separatorBorder: 'transparent' }
                    : undefined,
                disableAutoResizing: options.disableAutoResizing,
                className: options.className,
            });
            this._onDidLayoutfromJSON = new Emitter();
            this.onDidLayoutFromJSON = this._onDidLayoutfromJSON.event;
            this._onDidRemoveGroup = new Emitter();
            this.onDidRemoveGroup = this._onDidRemoveGroup.event;
            this._onDidAddGroup = new Emitter();
            this.onDidAddGroup = this._onDidAddGroup.event;
            this._onDidActiveGroupChange = new Emitter();
            this.onDidActiveGroupChange = this._onDidActiveGroupChange.event;
            this._options = options;
            this.addDisposables(this._onDidAddGroup, this._onDidRemoveGroup, this._onDidActiveGroupChange, this.onDidAdd((event) => {
                this._onDidAddGroup.fire(event);
            }), this.onDidRemove((event) => {
                this._onDidRemoveGroup.fire(event);
            }), this.onDidActiveChange((event) => {
                this._onDidActiveGroupChange.fire(event);
            }));
        }
        updateOptions(options) {
            super.updateOptions(options);
            const hasOrientationChanged = typeof options.orientation === 'string' &&
                this.gridview.orientation !== options.orientation;
            this._options = Object.assign(Object.assign({}, this.options), options);
            if (hasOrientationChanged) {
                this.gridview.orientation = options.orientation;
            }
            this.layout(this.gridview.width, this.gridview.height, true);
        }
        removePanel(panel) {
            this.removeGroup(panel);
        }
        /**
         * Serialize the current state of the layout
         *
         * @returns A JSON respresentation of the layout
         */
        toJSON() {
            var _a;
            const data = this.gridview.serialize();
            return {
                grid: data,
                activePanel: (_a = this.activeGroup) === null || _a === void 0 ? void 0 : _a.id,
            };
        }
        setVisible(panel, visible) {
            this.gridview.setViewVisible(getGridLocation(panel.element), visible);
        }
        setActive(panel) {
            this._groups.forEach((value, _key) => {
                value.value.setActive(panel === value.value);
            });
        }
        focus() {
            var _a;
            (_a = this.activeGroup) === null || _a === void 0 ? void 0 : _a.focus();
        }
        fromJSON(serializedGridview) {
            this.clear();
            const { grid, activePanel } = serializedGridview;
            try {
                const queue = [];
                // take note of the existing dimensions
                const width = this.width;
                const height = this.height;
                this.gridview.deserialize(grid, {
                    fromJSON: (node) => {
                        const { data } = node;
                        const view = this.options.createComponent({
                            id: data.id,
                            name: data.component,
                        });
                        queue.push(() => view.init({
                            params: data.params,
                            minimumWidth: data.minimumWidth,
                            maximumWidth: data.maximumWidth,
                            minimumHeight: data.minimumHeight,
                            maximumHeight: data.maximumHeight,
                            priority: data.priority,
                            snap: !!data.snap,
                            accessor: this,
                            isVisible: node.visible,
                        }));
                        this._onDidAddGroup.fire(view);
                        this.registerPanel(view);
                        return view;
                    },
                });
                this.layout(width, height, true);
                queue.forEach((f) => f());
                if (typeof activePanel === 'string') {
                    const panel = this.getPanel(activePanel);
                    if (panel) {
                        this.doSetGroupActive(panel);
                    }
                }
            }
            catch (err) {
                /**
                 * To remove a group we cannot call this.removeGroup(...) since this makes assumptions about
                 * the underlying HTMLElement existing in the Gridview.
                 */
                for (const group of this.groups) {
                    group.dispose();
                    this._groups.delete(group.id);
                    this._onDidRemoveGroup.fire(group);
                }
                // fires clean-up events and clears the underlying HTML gridview.
                this.clear();
                /**
                 * even though we have cleaned-up we still want to inform the caller of their error
                 * and we'll do this through re-throwing the original error since afterall you would
                 * expect trying to load a corrupted layout to result in an error and not silently fail...
                 */
                throw err;
            }
            this._onDidLayoutfromJSON.fire();
        }
        clear() {
            const hasActiveGroup = this.activeGroup;
            const groups = Array.from(this._groups.values()); // reassign since group panels will mutate
            for (const group of groups) {
                group.disposable.dispose();
                this.doRemoveGroup(group.value, { skipActive: true });
            }
            if (hasActiveGroup) {
                this.doSetGroupActive(undefined);
            }
            this.gridview.clear();
        }
        movePanel(panel, options) {
            var _a;
            let relativeLocation;
            const removedPanel = this.gridview.remove(panel);
            const referenceGroup = (_a = this._groups.get(options.reference)) === null || _a === void 0 ? void 0 : _a.value;
            if (!referenceGroup) {
                throw new Error(`reference group ${options.reference} does not exist`);
            }
            const target = toTarget(options.direction);
            if (target === 'center') {
                throw new Error(`${target} not supported as an option`);
            }
            else {
                const location = getGridLocation(referenceGroup.element);
                relativeLocation = getRelativeLocation(this.gridview.orientation, location, target);
            }
            this.doAddGroup(removedPanel, relativeLocation, options.size);
        }
        addPanel(options) {
            var _a, _b, _c, _d;
            let relativeLocation = (_a = options.location) !== null && _a !== void 0 ? _a : [0];
            if ((_b = options.position) === null || _b === void 0 ? void 0 : _b.referencePanel) {
                const referenceGroup = (_c = this._groups.get(options.position.referencePanel)) === null || _c === void 0 ? void 0 : _c.value;
                if (!referenceGroup) {
                    throw new Error(`reference group ${options.position.referencePanel} does not exist`);
                }
                const target = toTarget(options.position.direction);
                if (target === 'center') {
                    throw new Error(`${target} not supported as an option`);
                }
                else {
                    const location = getGridLocation(referenceGroup.element);
                    relativeLocation = getRelativeLocation(this.gridview.orientation, location, target);
                }
            }
            const view = this.options.createComponent({
                id: options.id,
                name: options.component,
            });
            view.init({
                params: (_d = options.params) !== null && _d !== void 0 ? _d : {},
                minimumWidth: options.minimumWidth,
                maximumWidth: options.maximumWidth,
                minimumHeight: options.minimumHeight,
                maximumHeight: options.maximumHeight,
                priority: options.priority,
                snap: !!options.snap,
                accessor: this,
                isVisible: true,
            });
            this.doAddGroup(view, relativeLocation, options.size);
            this.registerPanel(view);
            this.doSetGroupActive(view);
            return view;
        }
        registerPanel(panel) {
            const disposable = new CompositeDisposable(panel.api.onDidFocusChange((event) => {
                if (!event.isFocused) {
                    return;
                }
                this._groups.forEach((groupItem) => {
                    const group = groupItem.value;
                    if (group !== panel) {
                        group.setActive(false);
                    }
                    else {
                        group.setActive(true);
                    }
                });
            }));
            this._groups.set(panel.id, {
                value: panel,
                disposable,
            });
        }
        moveGroup(referenceGroup, groupId, target) {
            const sourceGroup = this.getPanel(groupId);
            if (!sourceGroup) {
                throw new Error('invalid operation');
            }
            const referenceLocation = getGridLocation(referenceGroup.element);
            const targetLocation = getRelativeLocation(this.gridview.orientation, referenceLocation, target);
            const [targetParentLocation, to] = tail(targetLocation);
            const sourceLocation = getGridLocation(sourceGroup.element);
            const [sourceParentLocation, from] = tail(sourceLocation);
            if (sequenceEquals(sourceParentLocation, targetParentLocation)) {
                // special case when 'swapping' two views within same grid location
                // if a group has one tab - we are essentially moving the 'group'
                // which is equivalent to swapping two views in this case
                this.gridview.moveView(sourceParentLocation, from, to);
                return;
            }
            // source group will become empty so delete the group
            const targetGroup = this.doRemoveGroup(sourceGroup, {
                skipActive: true,
                skipDispose: true,
            });
            // after deleting the group we need to re-evaulate the ref location
            const updatedReferenceLocation = getGridLocation(referenceGroup.element);
            const location = getRelativeLocation(this.gridview.orientation, updatedReferenceLocation, target);
            this.doAddGroup(targetGroup, location);
        }
        removeGroup(group) {
            super.removeGroup(group);
        }
        dispose() {
            super.dispose();
            this._onDidLayoutfromJSON.dispose();
        }
    }

    /**
     * A high-level implementation of splitview that works using 'panels'
     */
    class SplitviewComponent extends Resizable {
        get panels() {
            return this.splitview.getViews();
        }
        get options() {
            return this._options;
        }
        get length() {
            return this._panels.size;
        }
        get orientation() {
            return this.splitview.orientation;
        }
        get splitview() {
            return this._splitview;
        }
        set splitview(value) {
            if (this._splitview) {
                this._splitview.dispose();
            }
            this._splitview = value;
            this._splitviewChangeDisposable.value = new CompositeDisposable(this._splitview.onDidSashEnd(() => {
                this._onDidLayoutChange.fire(undefined);
            }), this._splitview.onDidAddView((e) => this._onDidAddView.fire(e)), this._splitview.onDidRemoveView((e) => this._onDidRemoveView.fire(e)));
        }
        get minimumSize() {
            return this.splitview.minimumSize;
        }
        get maximumSize() {
            return this.splitview.maximumSize;
        }
        get height() {
            return this.splitview.orientation === exports.Orientation.HORIZONTAL
                ? this.splitview.orthogonalSize
                : this.splitview.size;
        }
        get width() {
            return this.splitview.orientation === exports.Orientation.HORIZONTAL
                ? this.splitview.size
                : this.splitview.orthogonalSize;
        }
        constructor(container, options) {
            var _a;
            super(document.createElement('div'), options.disableAutoResizing);
            this._splitviewChangeDisposable = new MutableDisposable();
            this._panels = new Map();
            this._onDidLayoutfromJSON = new Emitter();
            this.onDidLayoutFromJSON = this._onDidLayoutfromJSON.event;
            this._onDidAddView = new Emitter();
            this.onDidAddView = this._onDidAddView.event;
            this._onDidRemoveView = new Emitter();
            this.onDidRemoveView = this._onDidRemoveView.event;
            this._onDidLayoutChange = new Emitter();
            this.onDidLayoutChange = this._onDidLayoutChange.event;
            this.element.style.height = '100%';
            this.element.style.width = '100%';
            this._classNames = new Classnames(this.element);
            this._classNames.setClassNames((_a = options.className) !== null && _a !== void 0 ? _a : '');
            // the container is owned by the third-party, do not modify/delete it
            container.appendChild(this.element);
            this._options = options;
            this.splitview = new Splitview(this.element, options);
            this.addDisposables(this._onDidAddView, this._onDidLayoutfromJSON, this._onDidRemoveView, this._onDidLayoutChange);
        }
        updateOptions(options) {
            var _a, _b;
            if ('className' in options) {
                this._classNames.setClassNames((_a = options.className) !== null && _a !== void 0 ? _a : '');
            }
            if ('disableResizing' in options) {
                this.disableResizing = (_b = options.disableAutoResizing) !== null && _b !== void 0 ? _b : false;
            }
            if (typeof options.orientation === 'string') {
                this.splitview.orientation = options.orientation;
            }
            this._options = Object.assign(Object.assign({}, this.options), options);
            this.splitview.layout(this.splitview.size, this.splitview.orthogonalSize);
        }
        focus() {
            var _a;
            (_a = this._activePanel) === null || _a === void 0 ? void 0 : _a.focus();
        }
        movePanel(from, to) {
            this.splitview.moveView(from, to);
        }
        setVisible(panel, visible) {
            const index = this.panels.indexOf(panel);
            this.splitview.setViewVisible(index, visible);
        }
        setActive(panel, skipFocus) {
            this._activePanel = panel;
            this.panels
                .filter((v) => v !== panel)
                .forEach((v) => {
                v.api._onDidActiveChange.fire({ isActive: false });
                if (!skipFocus) {
                    v.focus();
                }
            });
            panel.api._onDidActiveChange.fire({ isActive: true });
            if (!skipFocus) {
                panel.focus();
            }
        }
        removePanel(panel, sizing) {
            const item = this._panels.get(panel.id);
            if (!item) {
                throw new Error(`unknown splitview panel ${panel.id}`);
            }
            item.dispose();
            this._panels.delete(panel.id);
            const index = this.panels.findIndex((_) => _ === panel);
            const removedView = this.splitview.removeView(index, sizing);
            removedView.dispose();
            const panels = this.panels;
            if (panels.length > 0) {
                this.setActive(panels[panels.length - 1]);
            }
        }
        getPanel(id) {
            return this.panels.find((view) => view.id === id);
        }
        addPanel(options) {
            var _a;
            if (this._panels.has(options.id)) {
                throw new Error(`panel ${options.id} already exists`);
            }
            const view = this.options.createComponent({
                id: options.id,
                name: options.component,
            });
            view.orientation = this.splitview.orientation;
            view.init({
                params: (_a = options.params) !== null && _a !== void 0 ? _a : {},
                minimumSize: options.minimumSize,
                maximumSize: options.maximumSize,
                snap: options.snap,
                priority: options.priority,
                accessor: this,
            });
            const size = typeof options.size === 'number' ? options.size : exports.Sizing.Distribute;
            const index = typeof options.index === 'number' ? options.index : undefined;
            this.splitview.addView(view, size, index);
            this.doAddView(view);
            this.setActive(view);
            return view;
        }
        layout(width, height) {
            const [size, orthogonalSize] = this.splitview.orientation === exports.Orientation.HORIZONTAL
                ? [width, height]
                : [height, width];
            this.splitview.layout(size, orthogonalSize);
        }
        doAddView(view) {
            const disposable = view.api.onDidFocusChange((event) => {
                if (!event.isFocused) {
                    return;
                }
                this.setActive(view, true);
            });
            this._panels.set(view.id, disposable);
        }
        toJSON() {
            var _a;
            const views = this.splitview
                .getViews()
                .map((view, i) => {
                const size = this.splitview.getViewSize(i);
                return {
                    size,
                    data: view.toJSON(),
                    snap: !!view.snap,
                    priority: view.priority,
                };
            });
            return {
                views,
                activeView: (_a = this._activePanel) === null || _a === void 0 ? void 0 : _a.id,
                size: this.splitview.size,
                orientation: this.splitview.orientation,
            };
        }
        fromJSON(serializedSplitview) {
            this.clear();
            const { views, orientation, size, activeView } = serializedSplitview;
            const queue = [];
            // take note of the existing dimensions
            const width = this.width;
            const height = this.height;
            this.splitview = new Splitview(this.element, {
                orientation,
                proportionalLayout: this.options.proportionalLayout,
                descriptor: {
                    size,
                    views: views.map((view) => {
                        const data = view.data;
                        if (this._panels.has(data.id)) {
                            throw new Error(`panel ${data.id} already exists`);
                        }
                        const panel = this.options.createComponent({
                            id: data.id,
                            name: data.component,
                        });
                        queue.push(() => {
                            var _a;
                            panel.init({
                                params: (_a = data.params) !== null && _a !== void 0 ? _a : {},
                                minimumSize: data.minimumSize,
                                maximumSize: data.maximumSize,
                                snap: view.snap,
                                priority: view.priority,
                                accessor: this,
                            });
                        });
                        panel.orientation = orientation;
                        this.doAddView(panel);
                        setTimeout(() => {
                            // the original onDidAddView events are missed since they are fired before we can subcribe to them
                            this._onDidAddView.fire(panel);
                        }, 0);
                        return { size: view.size, view: panel };
                    }),
                },
            });
            this.layout(width, height);
            queue.forEach((f) => f());
            if (typeof activeView === 'string') {
                const panel = this.getPanel(activeView);
                if (panel) {
                    this.setActive(panel);
                }
            }
            this._onDidLayoutfromJSON.fire();
        }
        clear() {
            for (const disposable of this._panels.values()) {
                disposable.dispose();
            }
            this._panels.clear();
            while (this.splitview.length > 0) {
                const view = this.splitview.removeView(0, exports.Sizing.Distribute, true);
                view.dispose();
            }
        }
        dispose() {
            for (const disposable of this._panels.values()) {
                disposable.dispose();
            }
            this._panels.clear();
            const views = this.splitview.getViews();
            this._splitviewChangeDisposable.dispose();
            this.splitview.dispose();
            for (const view of views) {
                view.dispose();
            }
            this.element.remove();
            super.dispose();
        }
    }

    class DefaultHeader extends CompositeDisposable {
        get element() {
            return this._element;
        }
        constructor() {
            super();
            this._expandedIcon = createExpandMoreButton();
            this._collapsedIcon = createChevronRightButton();
            this.disposable = new MutableDisposable();
            this.apiRef = {
                api: null,
            };
            this._element = document.createElement('div');
            this.element.className = 'dv-default-header';
            this._content = document.createElement('span');
            this._expander = document.createElement('div');
            this._expander.className = 'dv-pane-header-icon';
            this.element.appendChild(this._expander);
            this.element.appendChild(this._content);
            this.addDisposables(addDisposableListener(this._element, 'click', () => {
                var _a;
                (_a = this.apiRef.api) === null || _a === void 0 ? void 0 : _a.setExpanded(!this.apiRef.api.isExpanded);
            }));
        }
        init(params) {
            this.apiRef.api = params.api;
            this._content.textContent = params.title;
            this.updateIcon();
            this.disposable.value = params.api.onDidExpansionChange(() => {
                this.updateIcon();
            });
        }
        updateIcon() {
            var _a;
            const isExpanded = !!((_a = this.apiRef.api) === null || _a === void 0 ? void 0 : _a.isExpanded);
            toggleClass(this._expander, 'collapsed', !isExpanded);
            if (isExpanded) {
                if (this._expander.contains(this._collapsedIcon)) {
                    this._collapsedIcon.remove();
                }
                if (!this._expander.contains(this._expandedIcon)) {
                    this._expander.appendChild(this._expandedIcon);
                }
            }
            else {
                if (this._expander.contains(this._expandedIcon)) {
                    this._expandedIcon.remove();
                }
                if (!this._expander.contains(this._collapsedIcon)) {
                    this._expander.appendChild(this._collapsedIcon);
                }
            }
        }
        update(_params) {
            //
        }
        dispose() {
            this.disposable.dispose();
            super.dispose();
        }
    }

    const nextLayoutId = sequentialNumberGenerator();
    const HEADER_SIZE = 22;
    const MINIMUM_BODY_SIZE = 0;
    const MAXIMUM_BODY_SIZE = Number.MAX_SAFE_INTEGER;
    class PaneFramework extends DraggablePaneviewPanel {
        constructor(options) {
            super({
                accessor: options.accessor,
                id: options.id,
                component: options.component,
                headerComponent: options.headerComponent,
                orientation: options.orientation,
                isExpanded: options.isExpanded,
                disableDnd: options.disableDnd,
                headerSize: options.headerSize,
                minimumBodySize: options.minimumBodySize,
                maximumBodySize: options.maximumBodySize,
            });
            this.options = options;
        }
        getBodyComponent() {
            return this.options.body;
        }
        getHeaderComponent() {
            return this.options.header;
        }
    }
    class PaneviewComponent extends Resizable {
        get id() {
            return this._id;
        }
        get panels() {
            return this.paneview.getPanes();
        }
        set paneview(value) {
            this._paneview = value;
            this._disposable.value = new CompositeDisposable(this._paneview.onDidChange(() => {
                this._onDidLayoutChange.fire(undefined);
            }), this._paneview.onDidAddView((e) => this._onDidAddView.fire(e)), this._paneview.onDidRemoveView((e) => this._onDidRemoveView.fire(e)));
        }
        get paneview() {
            return this._paneview;
        }
        get minimumSize() {
            return this.paneview.minimumSize;
        }
        get maximumSize() {
            return this.paneview.maximumSize;
        }
        get height() {
            return this.paneview.orientation === exports.Orientation.HORIZONTAL
                ? this.paneview.orthogonalSize
                : this.paneview.size;
        }
        get width() {
            return this.paneview.orientation === exports.Orientation.HORIZONTAL
                ? this.paneview.size
                : this.paneview.orthogonalSize;
        }
        get options() {
            return this._options;
        }
        constructor(container, options) {
            var _a;
            super(document.createElement('div'), options.disableAutoResizing);
            this._id = nextLayoutId.next();
            this._disposable = new MutableDisposable();
            this._viewDisposables = new Map();
            this._onDidLayoutfromJSON = new Emitter();
            this.onDidLayoutFromJSON = this._onDidLayoutfromJSON.event;
            this._onDidLayoutChange = new Emitter();
            this.onDidLayoutChange = this._onDidLayoutChange.event;
            this._onDidDrop = new Emitter();
            this.onDidDrop = this._onDidDrop.event;
            this._onDidAddView = new Emitter();
            this.onDidAddView = this._onDidAddView.event;
            this._onDidRemoveView = new Emitter();
            this.onDidRemoveView = this._onDidRemoveView.event;
            this._onUnhandledDragOverEvent = new Emitter();
            this.onUnhandledDragOverEvent = this._onUnhandledDragOverEvent.event;
            this.element.style.height = '100%';
            this.element.style.width = '100%';
            this.addDisposables(this._onDidLayoutChange, this._onDidLayoutfromJSON, this._onDidDrop, this._onDidAddView, this._onDidRemoveView, this._onUnhandledDragOverEvent);
            this._classNames = new Classnames(this.element);
            this._classNames.setClassNames((_a = options.className) !== null && _a !== void 0 ? _a : '');
            // the container is owned by the third-party, do not modify/delete it
            container.appendChild(this.element);
            this._options = options;
            this.paneview = new Paneview(this.element, {
                // only allow paneview in the vertical orientation for now
                orientation: exports.Orientation.VERTICAL,
            });
            this.addDisposables(this._disposable);
        }
        setVisible(panel, visible) {
            const index = this.panels.indexOf(panel);
            this.paneview.setViewVisible(index, visible);
        }
        focus() {
            //noop
        }
        updateOptions(options) {
            var _a, _b;
            if ('className' in options) {
                this._classNames.setClassNames((_a = options.className) !== null && _a !== void 0 ? _a : '');
            }
            if ('disableResizing' in options) {
                this.disableResizing = (_b = options.disableAutoResizing) !== null && _b !== void 0 ? _b : false;
            }
            this._options = Object.assign(Object.assign({}, this.options), options);
        }
        addPanel(options) {
            var _a, _b;
            const body = this.options.createComponent({
                id: options.id,
                name: options.component,
            });
            let header;
            if (options.headerComponent && this.options.createHeaderComponent) {
                header = this.options.createHeaderComponent({
                    id: options.id,
                    name: options.headerComponent,
                });
            }
            if (!header) {
                header = new DefaultHeader();
            }
            const view = new PaneFramework({
                id: options.id,
                component: options.component,
                headerComponent: options.headerComponent,
                header,
                body,
                orientation: exports.Orientation.VERTICAL,
                isExpanded: !!options.isExpanded,
                disableDnd: !!this.options.disableDnd,
                accessor: this,
                headerSize: (_a = options.headerSize) !== null && _a !== void 0 ? _a : HEADER_SIZE,
                minimumBodySize: MINIMUM_BODY_SIZE,
                maximumBodySize: MAXIMUM_BODY_SIZE,
            });
            this.doAddPanel(view);
            const size = typeof options.size === 'number' ? options.size : exports.Sizing.Distribute;
            const index = typeof options.index === 'number' ? options.index : undefined;
            view.init({
                params: (_b = options.params) !== null && _b !== void 0 ? _b : {},
                minimumBodySize: options.minimumBodySize,
                maximumBodySize: options.maximumBodySize,
                isExpanded: options.isExpanded,
                title: options.title,
                containerApi: new PaneviewApi(this),
                accessor: this,
            });
            this.paneview.addPane(view, size, index);
            view.orientation = this.paneview.orientation;
            return view;
        }
        removePanel(panel) {
            const views = this.panels;
            const index = views.findIndex((_) => _ === panel);
            this.paneview.removePane(index);
            this.doRemovePanel(panel);
        }
        movePanel(from, to) {
            this.paneview.moveView(from, to);
        }
        getPanel(id) {
            return this.panels.find((view) => view.id === id);
        }
        layout(width, height) {
            const [size, orthogonalSize] = this.paneview.orientation === exports.Orientation.HORIZONTAL
                ? [width, height]
                : [height, width];
            this.paneview.layout(size, orthogonalSize);
        }
        toJSON() {
            const maximum = (value) => value === Number.MAX_SAFE_INTEGER ||
                value === Number.POSITIVE_INFINITY
                ? undefined
                : value;
            const minimum = (value) => (value <= 0 ? undefined : value);
            const views = this.paneview
                .getPanes()
                .map((view, i) => {
                const size = this.paneview.getViewSize(i);
                return {
                    size,
                    data: view.toJSON(),
                    minimumSize: minimum(view.minimumBodySize),
                    maximumSize: maximum(view.maximumBodySize),
                    headerSize: view.headerSize,
                    expanded: view.isExpanded(),
                };
            });
            return {
                views,
                size: this.paneview.size,
            };
        }
        fromJSON(serializedPaneview) {
            this.clear();
            const { views, size } = serializedPaneview;
            const queue = [];
            // take note of the existing dimensions
            const width = this.width;
            const height = this.height;
            this.paneview = new Paneview(this.element, {
                orientation: exports.Orientation.VERTICAL,
                descriptor: {
                    size,
                    views: views.map((view) => {
                        var _a, _b, _c;
                        const data = view.data;
                        const body = this.options.createComponent({
                            id: data.id,
                            name: data.component,
                        });
                        let header;
                        if (data.headerComponent &&
                            this.options.createHeaderComponent) {
                            header = this.options.createHeaderComponent({
                                id: data.id,
                                name: data.headerComponent,
                            });
                        }
                        if (!header) {
                            header = new DefaultHeader();
                        }
                        const panel = new PaneFramework({
                            id: data.id,
                            component: data.component,
                            headerComponent: data.headerComponent,
                            header,
                            body,
                            orientation: exports.Orientation.VERTICAL,
                            isExpanded: !!view.expanded,
                            disableDnd: !!this.options.disableDnd,
                            accessor: this,
                            headerSize: (_a = view.headerSize) !== null && _a !== void 0 ? _a : HEADER_SIZE,
                            minimumBodySize: (_b = view.minimumSize) !== null && _b !== void 0 ? _b : MINIMUM_BODY_SIZE,
                            maximumBodySize: (_c = view.maximumSize) !== null && _c !== void 0 ? _c : MAXIMUM_BODY_SIZE,
                        });
                        this.doAddPanel(panel);
                        queue.push(() => {
                            var _a;
                            panel.init({
                                params: (_a = data.params) !== null && _a !== void 0 ? _a : {},
                                minimumBodySize: view.minimumSize,
                                maximumBodySize: view.maximumSize,
                                title: data.title,
                                isExpanded: !!view.expanded,
                                containerApi: new PaneviewApi(this),
                                accessor: this,
                            });
                            panel.orientation = this.paneview.orientation;
                        });
                        setTimeout(() => {
                            // the original onDidAddView events are missed since they are fired before we can subcribe to them
                            this._onDidAddView.fire(panel);
                        }, 0);
                        return { size: view.size, view: panel };
                    }),
                },
            });
            this.layout(width, height);
            queue.forEach((f) => f());
            this._onDidLayoutfromJSON.fire();
        }
        clear() {
            for (const [_, value] of this._viewDisposables.entries()) {
                value.dispose();
            }
            this._viewDisposables.clear();
            this.paneview.dispose();
        }
        doAddPanel(panel) {
            const disposable = new CompositeDisposable(panel.onDidDrop((event) => {
                this._onDidDrop.fire(event);
            }), panel.onUnhandledDragOverEvent((event) => {
                this._onUnhandledDragOverEvent.fire(event);
            }));
            this._viewDisposables.set(panel.id, disposable);
        }
        doRemovePanel(panel) {
            const disposable = this._viewDisposables.get(panel.id);
            if (disposable) {
                disposable.dispose();
                this._viewDisposables.delete(panel.id);
            }
        }
        dispose() {
            super.dispose();
            for (const [_, value] of this._viewDisposables.entries()) {
                value.dispose();
            }
            this._viewDisposables.clear();
            this.element.remove();
            this.paneview.dispose();
        }
    }

    class SplitviewPanel extends BasePanelView {
        get priority() {
            return this._priority;
        }
        set orientation(value) {
            this._orientation = value;
        }
        get orientation() {
            return this._orientation;
        }
        get minimumSize() {
            const size = typeof this._minimumSize === 'function'
                ? this._minimumSize()
                : this._minimumSize;
            if (size !== this._evaluatedMinimumSize) {
                this._evaluatedMinimumSize = size;
                this.updateConstraints();
            }
            return size;
        }
        get maximumSize() {
            const size = typeof this._maximumSize === 'function'
                ? this._maximumSize()
                : this._maximumSize;
            if (size !== this._evaluatedMaximumSize) {
                this._evaluatedMaximumSize = size;
                this.updateConstraints();
            }
            return size;
        }
        get snap() {
            return this._snap;
        }
        constructor(id, componentName) {
            super(id, componentName, new SplitviewPanelApiImpl(id, componentName));
            this._evaluatedMinimumSize = 0;
            this._evaluatedMaximumSize = Number.POSITIVE_INFINITY;
            this._minimumSize = 0;
            this._maximumSize = Number.POSITIVE_INFINITY;
            this._snap = false;
            this._onDidChange = new Emitter();
            this.onDidChange = this._onDidChange.event;
            this.api.initialize(this);
            this.addDisposables(this._onDidChange, this.api.onWillVisibilityChange((event) => {
                const { isVisible } = event;
                const { accessor } = this._params;
                accessor.setVisible(this, isVisible);
            }), this.api.onActiveChange(() => {
                const { accessor } = this._params;
                accessor.setActive(this);
            }), this.api.onDidConstraintsChangeInternal((event) => {
                if (typeof event.minimumSize === 'number' ||
                    typeof event.minimumSize === 'function') {
                    this._minimumSize = event.minimumSize;
                }
                if (typeof event.maximumSize === 'number' ||
                    typeof event.maximumSize === 'function') {
                    this._maximumSize = event.maximumSize;
                }
                this.updateConstraints();
            }), this.api.onDidSizeChange((event) => {
                this._onDidChange.fire({ size: event.size });
            }));
        }
        setVisible(isVisible) {
            this.api._onDidVisibilityChange.fire({ isVisible });
        }
        setActive(isActive) {
            this.api._onDidActiveChange.fire({ isActive });
        }
        layout(size, orthogonalSize) {
            const [width, height] = this.orientation === exports.Orientation.HORIZONTAL
                ? [size, orthogonalSize]
                : [orthogonalSize, size];
            super.layout(width, height);
        }
        init(parameters) {
            super.init(parameters);
            this._priority = parameters.priority;
            if (parameters.minimumSize) {
                this._minimumSize = parameters.minimumSize;
            }
            if (parameters.maximumSize) {
                this._maximumSize = parameters.maximumSize;
            }
            if (parameters.snap) {
                this._snap = parameters.snap;
            }
        }
        toJSON() {
            const maximum = (value) => value === Number.MAX_SAFE_INTEGER ||
                value === Number.POSITIVE_INFINITY
                ? undefined
                : value;
            const minimum = (value) => (value <= 0 ? undefined : value);
            return Object.assign(Object.assign({}, super.toJSON()), { minimumSize: minimum(this.minimumSize), maximumSize: maximum(this.maximumSize) });
        }
        updateConstraints() {
            this.api._onDidConstraintsChange.fire({
                maximumSize: this._evaluatedMaximumSize,
                minimumSize: this._evaluatedMinimumSize,
            });
        }
    }

    function createDockview(element, options) {
        const component = new DockviewComponent(element, options);
        return component.api;
    }
    function createSplitview(element, options) {
        const component = new SplitviewComponent(element, options);
        return new SplitviewApi(component);
    }
    function createGridview(element, options) {
        const component = new GridviewComponent(element, options);
        return new GridviewApi(component);
    }
    function createPaneview(element, options) {
        const component = new PaneviewComponent(element, options);
        return new PaneviewApi(component);
    }

    exports.BaseGrid = BaseGrid;
    exports.ContentContainer = ContentContainer;
    exports.DEFAULT_TAB_GROUP_COLORS = DEFAULT_TAB_GROUP_COLORS;
    exports.DefaultDockviewDeserialzier = DefaultDockviewDeserialzier;
    exports.DefaultTab = DefaultTab;
    exports.DockviewApi = DockviewApi;
    exports.DockviewComponent = DockviewComponent;
    exports.DockviewCompositeDisposable = CompositeDisposable;
    exports.DockviewDidDropEvent = DockviewDidDropEvent;
    exports.DockviewEmitter = Emitter;
    exports.DockviewGroupPanel = DockviewGroupPanel;
    exports.DockviewGroupPanelModel = DockviewGroupPanelModel;
    exports.DockviewMutableDisposable = MutableDisposable;
    exports.DockviewPanel = DockviewPanel;
    exports.DockviewUnhandledDragOverEvent = DockviewUnhandledDragOverEvent;
    exports.DockviewWillDropEvent = DockviewWillDropEvent;
    exports.DockviewWillShowOverlayLocationEvent = DockviewWillShowOverlayLocationEvent;
    exports.DraggablePaneviewPanel = DraggablePaneviewPanel;
    exports.Gridview = Gridview;
    exports.GridviewApi = GridviewApi;
    exports.GridviewComponent = GridviewComponent;
    exports.GridviewPanel = GridviewPanel;
    exports.PROPERTY_KEYS_DOCKVIEW = PROPERTY_KEYS_DOCKVIEW;
    exports.PROPERTY_KEYS_GRIDVIEW = PROPERTY_KEYS_GRIDVIEW;
    exports.PROPERTY_KEYS_PANEVIEW = PROPERTY_KEYS_PANEVIEW;
    exports.PROPERTY_KEYS_SPLITVIEW = PROPERTY_KEYS_SPLITVIEW;
    exports.PaneFramework = PaneFramework;
    exports.PaneTransfer = PaneTransfer;
    exports.PanelTransfer = PanelTransfer;
    exports.Paneview = Paneview;
    exports.PaneviewApi = PaneviewApi;
    exports.PaneviewComponent = PaneviewComponent;
    exports.PaneviewPanel = PaneviewPanel;
    exports.PaneviewUnhandledDragOverEvent = PaneviewUnhandledDragOverEvent;
    exports.Splitview = Splitview;
    exports.SplitviewApi = SplitviewApi;
    exports.SplitviewComponent = SplitviewComponent;
    exports.SplitviewPanel = SplitviewPanel;
    exports.Tab = Tab;
    exports.TabGroupColorPalette = TabGroupColorPalette;
    exports.applyTabGroupAccent = applyTabGroupAccent;
    exports.createDockview = createDockview;
    exports.createGridview = createGridview;
    exports.createPaneview = createPaneview;
    exports.createSplitview = createSplitview;
    exports.directionToPosition = directionToPosition;
    exports.getDirectionOrientation = getDirectionOrientation;
    exports.getGridLocation = getGridLocation;
    exports.getLocationOrientation = getLocationOrientation;
    exports.getPaneData = getPaneData;
    exports.getPanelData = getPanelData;
    exports.getRelativeLocation = getRelativeLocation;
    exports.indexInParent = indexInParent;
    exports.isGridBranchNode = isGridBranchNode;
    exports.isGroupOptionsWithGroup = isGroupOptionsWithGroup;
    exports.isGroupOptionsWithPanel = isGroupOptionsWithPanel;
    exports.isPanelOptionsWithGroup = isPanelOptionsWithGroup;
    exports.isPanelOptionsWithPanel = isPanelOptionsWithPanel;
    exports.orthogonal = orthogonal;
    exports.positionToDirection = positionToDirection;
    exports.resolveTabGroupAccent = resolveTabGroupAccent;
    exports.themeAbyss = themeAbyss;
    exports.themeAbyssSpaced = themeAbyssSpaced;
    exports.themeCatppuccinMocha = themeCatppuccinMocha;
    exports.themeCatppuccinMochaSpaced = themeCatppuccinMochaSpaced;
    exports.themeDark = themeDark;
    exports.themeDracula = themeDracula;
    exports.themeGithubDark = themeGithubDark;
    exports.themeGithubDarkSpaced = themeGithubDarkSpaced;
    exports.themeGithubLight = themeGithubLight;
    exports.themeGithubLightSpaced = themeGithubLightSpaced;
    exports.themeLight = themeLight;
    exports.themeLightSpaced = themeLightSpaced;
    exports.themeMonokai = themeMonokai;
    exports.themeNord = themeNord;
    exports.themeNordSpaced = themeNordSpaced;
    exports.themeSolarizedLight = themeSolarizedLight;
    exports.themeSolarizedLightSpaced = themeSolarizedLightSpaced;
    exports.themeVisualStudio = themeVisualStudio;
    exports.toTarget = toTarget;

}));
