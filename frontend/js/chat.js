angular.module('planAssistant')
  .factory('homeService', function ($http, API_BASE) {
    return {
      load: function () {
        return $http.get(API_BASE + '/home').then(function (res) { return res.data; });
      }
    };
  })
  .factory('chatService', function ($http, API_BASE) {
    function unwrap(res) { return res.data; }
    return {
      send: function (message, context, sessionId) {
        return $http.post(API_BASE + '/chat', { message: message, context: context || null, session_id: sessionId })
          .then(function (res) { return res.data.reply; });
      },
      sessions: function () {
        return $http.get(API_BASE + '/chat/sessions').then(unwrap).then(function (d) { return d.sessions; });
      },
      history: function (sessionId) {
        return $http.get(API_BASE + '/chat/sessions/' + sessionId).then(unwrap).then(function (d) { return d.history; });
      },
      rename: function (sessionId, title) {
        return $http.patch(API_BASE + '/chat/sessions/' + sessionId, { title: title }).then(unwrap);
      },
      remove: function (sessionId) {
        return $http.delete(API_BASE + '/chat/sessions/' + sessionId);
      }
    };
  })
  .controller('ChatCtrl', function ($timeout, chatService, homeService) {
    var vm = this;
    vm.messages = [];
    vm.sessions = [];
    vm.groups = [];
    vm.sessionId = newSessionId();
    vm.input = '';
    vm.sending = false;
    vm.loadingHistory = false;
    vm.error = null;
    vm.sidebarError = null;
    vm.sidebarOpen = false;
    vm.editingId = null;
    vm.editTitle = '';
    vm.home = null;
    vm.homeError = null;

    function newSessionId() {
      var bytes = new Uint8Array(16);
      (window.crypto || window.msCrypto).getRandomValues(bytes);
      return 's' + Array.prototype.map.call(bytes, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
    }
    function scrollDown() {
      $timeout(function () {
        var el = document.getElementById('chat-scroll');
        if (el) { el.scrollTop = el.scrollHeight; }
      });
    }
    function toMessage(m) {
      return { role: m.role, text: m.content, time: m.time ? new Date(m.time * 1000).toISOString() : null };
    }
    function groupSessions(list) {
      var startOfToday = new Date(); startOfToday.setHours(0, 0, 0, 0);
      var day = 86400000;
      var buckets = [
        { label: 'Today', items: [], min: startOfToday.getTime() },
        { label: 'Yesterday', items: [], min: startOfToday.getTime() - day },
        { label: 'Previous 7 days', items: [], min: startOfToday.getTime() - 7 * day },
        { label: 'Older', items: [], min: -Infinity }
      ];
      list.forEach(function (s) {
        var t = s.updated_at * 1000;
        for (var i = 0; i < buckets.length; i++) {
          if (t >= buckets[i].min) { buckets[i].items.push(s); break; }
        }
      });
      return buckets.filter(function (b) { return b.items.length; });
    }

    vm.isSaved = function (id) {
      return vm.sessions.some(function (s) { return s.session_id === id; });
    };

    vm.refreshSessions = function () {
      return chatService.sessions()
        .then(function (list) { vm.sessions = list; vm.groups = groupSessions(list); vm.sidebarError = null; })
        .catch(function () { vm.sidebarError = 'Could not load your chats.'; })
        .finally(function () { vm.sessionsLoaded = true; });
    };

    vm.loadHome = function () {
      vm.homeError = null;
      homeService.load()
        .then(function (data) { vm.home = data; })
        .catch(function () { vm.homeError = 'Could not load your account summary.'; });
    };

    vm.open = function (id) {
      vm.retryOpen = null;
      if (id === vm.sessionId && !vm.error) { vm.sidebarOpen = false; return; }
      vm.sessionId = id;
      vm.messages = [];
      vm.error = null;
      vm.editingId = null;
      vm.sidebarOpen = false;
      vm.loadingHistory = true;
      chatService.history(id)
        .then(function (history) { if (vm.sessionId === id) { vm.messages = history.map(toMessage); } })
        .catch(function () { if (vm.sessionId === id) { vm.error = 'Could not load this chat.'; vm.retryOpen = id; } })
        .finally(function () { if (vm.sessionId === id) { vm.loadingHistory = false; scrollDown(); } });
    };

    vm.newChat = function () {
      vm.sessionId = newSessionId();
      vm.messages = [];
      vm.error = null;
      vm.editingId = null;
      vm.sidebarOpen = false;
      scrollDown();
    };

    vm.send = function (text, context) {
      text = (text || '').trim();
      var sid = vm.sessionId;
      if (!text || vm.sending || vm.loadingHistory) { return; }
      vm.error = null;
      vm.messages.push({ role: 'user', text: text, time: new Date().toISOString() });
      vm.input = '';
      vm.sending = true;
      scrollDown();
      chatService.send(text, context, sid)
        .then(function (reply) {
          if (vm.sessionId === sid) { vm.messages.push({ role: 'assistant', text: reply, time: new Date().toISOString() }); }
          return vm.refreshSessions();
        })
        .catch(function () {
          if (vm.sessionId === sid) { vm.error = 'Message failed to send.'; vm.lastFailed = { text: text, context: context }; }
        })
        .finally(function () { vm.sending = false; scrollDown(); });
    };

    vm.retry = function () {
      if (vm.retryOpen) { var id = vm.retryOpen; vm.retryOpen = null; vm.sessionId = null; vm.open(id); return; }
      var f = vm.lastFailed;
      vm.messages.pop();
      vm.send(f.text, f.context);
    };

    vm.startRename = function (s, $event) {
      $event.stopPropagation();
      vm.editingId = s.session_id;
      vm.editTitle = s.title;
      $timeout(function () { var el = document.getElementById('rename-input'); if (el) { el.focus(); el.select(); } });
    };
    vm.cancelRename = function () { vm.editingId = null; };
    vm.saveRename = function (s) {
      if (vm.editingId !== s.session_id) { return; }
      var title = (vm.editTitle || '').trim();
      vm.editingId = null;
      if (!title || title === s.title) { return; }
      chatService.rename(s.session_id, title)
        .then(function (res) { s.title = res.title; })
        .catch(function () { vm.sidebarError = 'Could not rename the chat.'; });
    };

    vm.remove = function (s, $event) {
      $event.stopPropagation();
      if (!window.confirm('Delete "' + s.title + '"? This cannot be undone.')) { return; }
      chatService.remove(s.session_id)
        .then(function () {
          var wasActive = vm.sessionId === s.session_id;
          return vm.refreshSessions().then(function () {
            if (wasActive) { vm.sessions.length ? vm.open(vm.sessions[0].session_id) : vm.newChat(); }
          });
        })
        .catch(function () { vm.sidebarError = 'Could not delete the chat.'; });
    };

    vm.onKey = function (e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); vm.send(vm.input); }
    };

    vm.loadHome();
    vm.refreshSessions().then(function () {
      if (vm.sessions.length) { vm.sessionId = null; vm.open(vm.sessions[0].session_id); }
    });
  });
