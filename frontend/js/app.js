angular.module('planAssistant', ['ngRoute'])
  .constant('API_BASE', 'http://localhost:8000/api/v1')
  .config(function ($routeProvider, $httpProvider) {
    $routeProvider
      .when('/login', {
        templateUrl: 'login.html',
        controller: 'LoginCtrl',
        controllerAs: 'vm'
      })
      .when('/chat', {
        templateUrl: 'chat.html',
        controller: 'ChatCtrl',
        controllerAs: 'vm',
        requireAuth: true
      })
      .otherwise({ redirectTo: '/chat' });
    $httpProvider.interceptors.push('authInterceptor');
  })
  .run(function ($rootScope, $location, authService) {
    $rootScope.$on('$routeChangeStart', function (event, next) {
      if (next.requireAuth && !authService.isLoggedIn()) {
        $location.path('/login');
      }
    });
  });
