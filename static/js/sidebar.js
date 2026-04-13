/* ============================================================
   ARGUS — Sidebar Toggle
   ============================================================ */

function toggleSidebar() {
    const sidebar = document.getElementById('sidebar');
    const isCollapsed = sidebar.classList.toggle('collapsed');
    localStorage.setItem('argusSidebarCollapsed', isCollapsed);
    const icon = document.getElementById('sidebarToggleIcon');
    if (icon) icon.textContent = isCollapsed ? '»' : '«';
}

function initSidebar() {
    const isCollapsed = localStorage.getItem('argusSidebarCollapsed') === 'true';
    const sidebar = document.getElementById('sidebar');
    const icon    = document.getElementById('sidebarToggleIcon');
    if (isCollapsed && sidebar) {
        sidebar.classList.add('collapsed');
        if (icon) icon.textContent = '»';
    }
}
