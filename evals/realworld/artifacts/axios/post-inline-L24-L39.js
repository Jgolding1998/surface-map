    (function () {
      var output = document.getElementById('output');
      document.getElementById('post').onclick = function () {
        var data = document.getElementById('data').value;

        axios.post('/post/server', JSON.parse(data))
          .then(function (res) {
            output.className = 'container';
            output.innerHTML = res.data;
          })
          .catch(function (err) {
            output.className = 'container text-danger';
            output.innerHTML = err.message;
          });
      };
    })();
