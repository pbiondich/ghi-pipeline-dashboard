/* GHI Win board — drag/drop, archive, filter, closed toggle */
(function () {
    'use strict';

    var draggedCard = null;

    function showToast(message, ok) {
        var el = document.getElementById('toast');
        if (!el) return;
        el.textContent = message;
        el.hidden = false;
        el.classList.toggle('toast-error', !ok);
        clearTimeout(showToast._t);
        showToast._t = setTimeout(function () {
            el.hidden = true;
        }, 4000);
    }

    window.onDragStart = function (event) {
        draggedCard = event.target.closest('.card');
        if (!draggedCard) return;
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('text/plain', draggedCard.dataset.slug);
        draggedCard.classList.add('dragging');
    };

    window.onDragOver = function (event) {
        event.preventDefault();
        event.dataTransfer.dropEffect = 'move';
        var column = event.target.closest('.column');
        if (column) {
            document.querySelectorAll('.column.drag-over').forEach(function (c) {
                c.classList.remove('drag-over');
            });
            column.classList.add('drag-over');
        }
    };

    window.onDrop = function (event) {
        event.preventDefault();
        document.querySelectorAll('.column.drag-over, .card.dragging').forEach(function (el) {
            el.classList.remove('drag-over', 'dragging');
        });

        var column = event.target.closest('.column');
        if (!column || !draggedCard) return;

        var newStatus = column.dataset.status;
        var oldStatus = draggedCard.dataset.status;
        var slug = draggedCard.dataset.slug;

        if (newStatus === oldStatus) return;

        var reason = '';
        if (newStatus === 'no-go') {
            reason = prompt('Why is this a no-go? (Optional)');
            if (reason === null) {
                draggedCard = null;
                return;
            }
        }

        var targetCards = column.querySelector('.column-cards');
        var emptyHint = targetCards.querySelector('.column-empty-hint');
        if (emptyHint) emptyHint.remove();
        targetCards.appendChild(draggedCard);
        draggedCard.dataset.status = newStatus;
        updateColumnCounts();

        fetch('/api/proposals/' + slug, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: newStatus, reason: reason })
        })
            .then(function (response) {
                if (!response.ok) {
                    moveCardBack(draggedCard, oldStatus);
                    showToast('Could not write status back to the vault.', false);
                } else {
                    showToast('Status written back to the vault.', true);
                }
            })
            .catch(function () {
                moveCardBack(draggedCard, oldStatus);
                showToast('Network error. Card moved back.', false);
            });

        draggedCard = null;
    };

    function moveCardBack(card, oldStatus) {
        var oldColumn = document.querySelector('.column[data-status="' + oldStatus + '"]');
        if (oldColumn && card) {
            oldColumn.querySelector('.column-cards').appendChild(card);
            card.dataset.status = oldStatus;
        }
        updateColumnCounts();
    }

    function updateColumnCounts() {
        document.querySelectorAll('.column').forEach(function (col) {
            var count = col.querySelectorAll('.card:not(.card-filtered-out)').length;
            var counter = col.querySelector('.column-count');
            if (counter) counter.textContent = count;
            col.classList.toggle('column-empty', col.querySelectorAll('.card').length === 0);
        });
    }

    function archiveCard(slug, status) {
        var reason = '';
        if (status === 'no-go') {
            reason = prompt('Why is this a no-go? (Optional)');
            if (reason === null) return;
        }

        var card = document.querySelector('.card[data-slug="' + slug + '"]');
        if (!card) return;

        var column = card.closest('.column');
        card.remove();
        updateColumnCounts();
        updateTotalActive();

        fetch('/api/proposals/' + slug, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: status, reason: reason })
        })
            .then(function (response) {
                if (!response.ok) {
                    if (column) {
                        column.querySelector('.column-cards').appendChild(card);
                        updateColumnCounts();
                        updateTotalActive();
                    }
                    showToast('Could not write status back to the vault.', false);
                } else {
                    location.reload();
                }
            })
            .catch(function () {
                if (column) {
                    column.querySelector('.column-cards').appendChild(card);
                    updateColumnCounts();
                    updateTotalActive();
                }
                showToast('Network error. Card restored.', false);
            });
    }

    function updateTotalActive() {
        var active = document.querySelectorAll('.column .card').length;
        var el = document.querySelector('.header-count');
        if (el) el.textContent = active + ' active';
    }

    document.addEventListener('click', function (event) {
        var btn = event.target.closest('.card-archive-btn');
        if (btn) {
            event.preventDefault();
            event.stopPropagation();
            var menu = btn.nextElementSibling;
            document.querySelectorAll('.archive-menu.show').forEach(function (m) {
                if (m !== menu) {
                    m.classList.remove('show');
                    m.hidden = true;
                    var other = m.previousElementSibling;
                    if (other) other.setAttribute('aria-expanded', 'false');
                }
            });
            var open = !menu.classList.contains('show');
            menu.classList.toggle('show', open);
            menu.hidden = !open;
            btn.setAttribute('aria-expanded', open ? 'true' : 'false');
            return;
        }

        var item = event.target.closest('.archive-menu-item');
        if (item) {
            event.preventDefault();
            var menu = item.closest('.archive-menu');
            var card = menu.closest('.card');
            if (card) {
                archiveCard(card.dataset.slug, item.dataset.status);
            }
            menu.classList.remove('show');
            menu.hidden = true;
            return;
        }

        document.querySelectorAll('.archive-menu.show').forEach(function (m) {
            m.classList.remove('show');
            m.hidden = true;
            var b = m.previousElementSibling;
            if (b) b.setAttribute('aria-expanded', 'false');
        });
    });

    function setClosedVisible(show) {
        var section = document.getElementById('archiveSection');
        var btn = document.getElementById('archiveBtn');
        if (!section || !btn) return;
        var count = typeof CLOSED_COUNT === 'number' ? CLOSED_COUNT : 0;
        if (show) {
            section.classList.remove('archive-hidden');
            section.style.display = 'block';
            section.setAttribute('aria-hidden', 'false');
            btn.textContent = 'Hide closed';
            btn.setAttribute('aria-expanded', 'true');
        } else {
            section.classList.add('archive-hidden');
            section.style.display = 'none';
            section.setAttribute('aria-hidden', 'true');
            btn.textContent = 'Show closed (' + count + ')';
            btn.setAttribute('aria-expanded', 'false');
        }
    }

    function toggleArchive(event) {
        if (event) {
            event.preventDefault();
            event.stopPropagation();
        }
        var section = document.getElementById('archiveSection');
        if (!section) return;
        var isVisible = !section.classList.contains('archive-hidden') &&
            section.style.display === 'block';
        setClosedVisible(!isVisible);
    }

    (function wireArchiveToggle() {
        function bind() {
            var btn = document.getElementById('archiveBtn');
            if (!btn || btn.dataset.bound === '1') return;
            btn.dataset.bound = '1';
            btn.addEventListener('click', toggleArchive, false);
        }
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', bind);
        } else {
            bind();
        }
    })();

    document.addEventListener('dragend', function () {
        document.querySelectorAll('.dragging, .drag-over').forEach(function (el) {
            el.classList.remove('dragging', 'drag-over');
        });
        draggedCard = null;
    });

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            document.querySelectorAll('.archive-menu.show').forEach(function (m) {
                m.classList.remove('show');
                m.hidden = true;
            });
        }
    });

    function applyFilter(query) {
        var q = (query || '').trim().toLowerCase();
        var cards = document.querySelectorAll('.pipeline .card');
        var shown = 0;
        cards.forEach(function (card) {
            var hay = card.dataset.search || card.textContent.toLowerCase();
            var match = !q || hay.indexOf(q) !== -1;
            card.classList.toggle('card-filtered-out', !match);
            if (match) shown += 1;
        });
        updateColumnCounts();
        var status = document.getElementById('filterStatus');
        if (!status) return;
        if (!q) {
            status.hidden = true;
            status.textContent = '';
            return;
        }
        status.hidden = false;
        status.textContent = shown + ' matching ' + (shown === 1 ? 'opportunity' : 'opportunities');
    }

    var search = document.getElementById('boardSearch');
    if (search) {
        search.addEventListener('input', function () {
            applyFilter(search.value);
        });
    }
})();
