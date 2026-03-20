{
  onEnter(log, args, state) {
    var len = args[2].toInt32();
    if (len >= 500 && len <= 548) {
      log('WriteFile len=' + len + ' report=0x' + args[1].readU8().toString(16));
      log(hexdump(args[1], {length: len}));
    }
  }
}
