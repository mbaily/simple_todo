// Resolve app routes against the public mount path, independent of the current page.
// The script URL includes the prefix even when Caddy strips it upstream.
(function () {
    if (window.SimpleTodo) return;
    const scriptPath = new URL(document.currentScript.src).pathname;
    const rootPath = scriptPath.slice(0, scriptPath.lastIndexOf('/static/'));

    function url(path) {
        if (/^(?:[a-z][a-z0-9+.-]*:|\/\/|#|\?)/i.test(path)) return path;
        if (!path.startsWith('/')) path = '/' + path;
        const pathname = path.split(/[?#]/, 1)[0];
        if (rootPath && (pathname === rootPath || pathname.startsWith(rootPath + '/'))) return path;
        return rootPath + path;
    }

    function routePath(path = window.location.pathname) {
        if (rootPath && path !== rootPath && !path.startsWith(rootPath + '/')) return null;
        return path.slice(rootPath.length) || '/';
    }

    window.SimpleTodo = {
        rootPath,
        url,
        routePath,
        fetch: (path, options) => window.fetch(url(path), options),
        // Preserve existing root-install storage names; isolate subpath installs.
        storageKey: name => name + (rootPath ? ':' + rootPath : '')
    };
})();
