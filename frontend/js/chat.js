angular.module('planAssistant')
  .factory('homeService', function ($http, API_BASE) {
    return {
      load: function () {
        return $http.get(API_BASE + '/home').then(function (res) { return res.data; });
      }
    };
  })
  .factory('chatService', function ($http, API_BASE) {
    return {
      send: function (message, context) {
        return $http.post(API_BASE + '/chat', { message: message, context: context || null })
          .then(function (res) { return res.data.reply; });
      }
    };
  })
  .controller('ChatCtrl', function ($timeout, chatService, homeService) {
    var vm = this;
    var HISTORY_KEY = 'pa_chat_history';
    vm.messages = JSON.parse(sessionStorage.getItem(HISTORY_KEY) || '[]');
    vm.input = '';
    vm.sending = false;
    vm.error = null;
    vm.home = null;
    vm.homeError = null;

    function save() { sessionStorage.setItem(HISTORY_KEY, JSON.stringify(vm.messages)); }
    function scrollDown() {
      $timeout(function () {
        var el = document.getElementById('chat-scroll');
        if (el) { el.scrollTop = el.scrollHeight; }
      });
    }

    vm.loadHome = function () {
      vm.homeError = null;
      homeService.load()
        .then(function (data) { vm.home = data; })
        .catch(function () { vm.homeError = 'Could not load your account summary.'; });
    };

    vm.send = function (text, context) {
      text = (text || '').trim();
      if (!text || vm.sending) { return; }
      vm.error = null;
      vm.messages.push({ role: 'user', text: text, time: new Date().toISOString() });
      vm.input = '';
      vm.sending = true;
      save();
      scrollDown();
      chatService.send(text, context)
        .then(function (reply) {
          vm.messages.push({ role: 'assistant', text: reply, time: new Date().toISOString() });
          save();
        })
        .catch(function () { vm.error = 'Message failed to send.'; vm.lastFailed = { text: text, context: context }; })
        .finally(function () { vm.sending = false; scrollDown(); });
    };

    vm.retry = function () {
      var f = vm.lastFailed;
      vm.messages.pop();
      save();
      vm.send(f.text, f.context);
    };

    vm.newChat = function () { vm.messages = []; vm.error = null; save(); scrollDown(); };

    vm.onKey = function (e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); vm.send(vm.input); }
    };

    vm.loadHome();
    scrollDown();
  });
