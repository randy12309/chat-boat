angular.module('planAssistant')
  .factory('authService', function ($http, API_BASE) {
    var TOKEN_KEY = 'pa_token';
    var USER_KEY = 'pa_user';
    return {
      login: function (username, password) {
        return $http.post(API_BASE + '/auth/login', { username: username, password: password })
          .then(function (res) {
            sessionStorage.setItem(TOKEN_KEY, res.data.access_token);
            sessionStorage.setItem(USER_KEY, res.data.username);
            return res.data;
          });
      },
      logout: function () {
        sessionStorage.removeItem(TOKEN_KEY);
        sessionStorage.removeItem(USER_KEY);
      },
      token: function () { return sessionStorage.getItem(TOKEN_KEY); },
      user: function () { return sessionStorage.getItem(USER_KEY); },
      isLoggedIn: function () { return !!sessionStorage.getItem(TOKEN_KEY); }
    };
  })
  .factory('authInterceptor', function ($q, $location, $injector) {
    return {
      request: function (config) {
        var token = $injector.get('authService').token();
        if (token) { config.headers.Authorization = 'Bearer ' + token; }
        return config;
      },
      responseError: function (rejection) {
        var isLogin = rejection.config && /\/auth\/login$/.test(rejection.config.url);
        if (rejection.status === 401 && !isLogin) {
          $injector.get('authService').logout();
          $location.path('/login');
        }
        return $q.reject(rejection);
      }
    };
  })
  .controller('LoginCtrl', function ($location, authService) {
    var vm = this;
    vm.username = '';
    vm.password = '';
    vm.error = null;
    vm.loading = false;
    vm.submit = function () {
      vm.error = null;
      vm.loading = true;
      authService.login(vm.username, vm.password)
        .then(function () { $location.path('/chat'); })
        .catch(function (err) {
          vm.error = err.status === 401 ? 'Invalid username or password.' : 'Unable to reach the server. Try again.';
        })
        .finally(function () { vm.loading = false; });
    };
  })
  .controller('NavCtrl', function ($location, authService) {
    this.auth = authService;
    this.logout = function () { authService.logout(); $location.path('/login'); };
  });
