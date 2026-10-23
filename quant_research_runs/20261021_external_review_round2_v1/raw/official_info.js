/* 当日合约 */
var stockDayContract = {
  dayContractUrl: sseQueryURL + "commonQuery.do", //下拉、当日合约接口
  daySelectParam: { //标的代码参数
    isPagination: true,
    sqlId: "SSE_ZQPZ_YSP_SOPTZSXT_BASIC_INFO_YSHQ_BDZQDM_L",
  },
  dayContractParam: { //当日合约参数
    "isPagination": true,
    "expireDate": "",
    "securityId": "",
    "sqlId": "SSE_ZQPZ_YSP_GGQQZSXT_XXPL_DRHY_SEARCH_L",
    "pageHelp.pageSize": 25,
    "pageHelp.pageNo": 1,
    "pageHelp.beginPage": 1,
    "pageHelp.endPage": 1,
    "pageHelp.cacheSize": 1,
  },
  selectObj: {
    "全部": "全部"
  },
  init: function () {
    this.loadEvents();
    this.stockSelect(); //获取标的代码
    this.getDayContract(1);
  },
  loadEvents: function () {
    var _this = this;
    var radioHtm = '<label><i class="iconfont iconcircle iconcirclecheckfull"></i><input type="radio" name="codeName" value="全部">全部</label>';
    $(".js_code .sse_searchInput").html(radioHtm);

    //期权到期日下拉框数据渲染
    var dateOption = [{
      "name": "全部",
      "value": "全部"
    }];
    bootstrapSelect({
      method: function () {
        //期权到期日数据渲染
        $(".js_date .selectpicker").html(addSelectOption(dateOption)).selectpicker('refresh').selectpicker('render');
        //下拉框改变触发查询
        $(".js_date .selectpicker").on('changed.bs.select', function (e) {
          _this.dayContractParam.expireDate = e.target.value && e.target.value != "全部" ? e.target.value.replace("-", "") : "";
          _this.getDayContract(1);
        });
      }
    });
    //标的代码单选框选中
    $(".js_code").on("click", "input", function () {
      var radioVal = $(this).val();
      $(".js_code .iconcirclecheckfull").removeClass("iconcirclecheckfull");
      $(this).parent().find(".iconcircle").addClass("iconcirclecheckfull");
      _this.dayContractParam.securityId = radioVal && radioVal != "全部" ? radioVal : "";
      _this.dayContractParam.expireDate = "";
      _this.getDayContract(1);
      //期权到期日数据
      var dateArr = _this.selectObj[radioVal].split(",");
      dateOption = radioVal && radioVal != "全部" ? [{
        "name": "全部",
        "value": "全部"
      }] : [];
      $(".js_date .selectpicker").html("");
      for (var i = 0; i < dateArr.length; i++) {
        var two = {
          "name": dateArr[i],
          "value": dateArr[i]
        };
        dateOption.push(two);
      }
      //期权到期日数据渲染
      $(".js_date .selectpicker").html(addSelectOption(dateOption)).selectpicker('refresh').selectpicker('render');
    })
    $(".js_preinfo").append('<div class="remarks"><span>* 价格单位：元</span></div>')
  },
  //获取下拉框代码数据
  stockSelect: function () {
    var _this = this;
    getJSONP({
      url: _this.dayContractUrl,
      data: _this.daySelectParam,
      type: "post",
      successCallback: function (data) {
        if (data && data.result && data.result.length > 0) {
          var htm = '<label><i class="iconfont iconcircle iconcirclecheckfull"></i><input type="radio" name="codeName" checked value="全部">全部</label>';
          $.each(data.result, function (k, v) {
            htm += '<label><i class="iconfont iconcircle"></i><input type="radio" name="codeName" value="' + v.UNDERLYING_SECURITY_ID + '">' + v.UNDERLYING_SECURITY_ID + '</label>';
            _this.selectObj[v.UNDERLYING_SECURITY_ID] = v.EXPIRE_DATE;
          })
          $(".js_code .sse_searchInput").html(htm);
        }
      }
    })
  },
  //当日合约接口
  getDayContract: function (pageIndex) {
    var _this = this;
    if (!paginationChange(_this.dayContractParam, pageIndex, '.js_preinfo')) { // 触发分页改变分页参数
      return
    }
    var htm = '<thead><tr>';
    htm += '<th class="text-nowrap">合约编码</th>';
    htm += '<th class="text-nowrap">合约交易代码</th>';
    htm += '<th class="text-nowrap">合约简称</th>';
    htm += '<th class="text-nowrap">标的券名称及代码</th>';
    htm += '<th class="text-nowrap">类型</th>';
    htm += '<th class="text-nowrap text-right">行权价</th>';
    htm += '<th class="text-nowrap text-right">合约单位</th>';
    htm += '<th class="text-nowrap">期权行权日</th>';
    htm += '<th class="text-nowrap">行权交收日</th>';
    htm += '<th class="text-nowrap">到期日</th>';
    htm += '<th class="text-nowrap">新挂</th>';
    htm += '<th class="text-nowrap text-right">涨停价格</th>';
    htm += '<th class="text-nowrap text-right">跌停价格</th>';
    htm += '<th class="text-nowrap text-right">前结算价</th>';
    htm += '<th class="text-nowrap">调整</th>';
    htm += '<th class="text-nowrap">停牌</th>';
    htm += '</tr></thead><tbody>';
    getJSONP({
      url: _this.dayContractUrl,
      data: _this.dayContractParam,
      type: "post",
      successCallback: function (data) {
        if (data && data.result && data.result.length > 0) {
          $.each(data.result, function (k, v) {
            htm += '<tr>';
            htm += '<td class="text-nowrap">' + v.SECURITY_ID + '</td>';
            htm += '<td class="text-nowrap">' + v.CONTRACT_ID + '</td>';
            htm += '<td class="text-nowrap">' + v.CONTRACT_SYMBOL + '</td>';
            htm += '<td class="text-nowrap">' + v.SECURITYNAMEBYID + '</td>';
            htm += '<td class="text-nowrap">' + v.CALL_OR_PUT + '</td>';
            htm += '<td class="text-nowrap text-right">' + v.EXERCISE_PRICE + '</td>';
            htm += '<td class="text-nowrap text-right">' + v.CONTRACT_UNIT + '</td>';
            htm += '<td class="text-nowrap">' + v.EXERCISE_DATE + '</td>';
            htm += '<td class="text-nowrap">' + v.DELIVERY_DATE + '</td>';
            htm += '<td class="text-nowrap">' + v.EXPIRE_DATE + '</td>';
            htm += '<td class="text-nowrap">' + v.CONTRACTFLAG + '</td>';
            htm += '<td class="text-nowrap text-right">' + v.DAILY_PRICE_UPLIMIT + '</td>';
            htm += '<td class="text-nowrap text-right">' + v.DAILY_PRICE_DOWNLIMIT + '</td>';
            htm += '<td class="text-nowrap text-right">' + v.SETTL_PRICE + '</td>';
            htm += '<td class="text-nowrap">' + v.CHANGEFLAG + '</td>';
            htm += '<td class="text-nowrap">' + v.DELISTFLAG + '</td>';
            htm += '</tr>';
          })
          htm += '</tbody>';
          $(".js_preinfo .table").html(htm);
          Page.navigation(".js_preinfo .pagination-box", data.pageHelp.pageCount, data.pageHelp.total, data.pageHelp.pageNo, data.pageHelp.pageSize, "stockDayContract.getDayContract");
          // $('.js_preinfo .pagination-box .change-pageSize').remove();
          // $('.js_preinfo').show();
        } else {
          htm += '<tr><td colspan="18">暂无数据</td></tr>'
          htm += '</tbody>';
          $(".js_preinfo .table").html(htm);
          $(".js_preinfo .pagination-box").html("");
        }
      },
      errCallback: function () {
        htm += '<tr><td colspan="18">暂无数据</td></tr>'
        htm += '</tbody>';
        $(".js_preinfo .table").html(htm);
        $(".js_preinfo .pagination-box").html("");
      }
    })
  }
}
if ($(".js_preinfo").length > 0) {
  stockDayContract.init();
}
/* 挂牌信息 */
var stockListing = {
  systemDate: get_systemDate_global(),
  listingUrl: sseQueryURL + "commonQuery.do",
  newlyLinkedParam: { //新挂参数
    isPagination: false,
    sqlId: "SSE_ZQPZ_YSP_OPTZSXT_ADJUST_INFO_HYXG_SEARCH_L",
    adjustDate: "",
    securityCode: ""
  },
  delistParam: { //摘牌参数
    isPagination: false,
    sqlId: "SSE_ZQPZ_YSP_OPTZSXT_ADJUST_INFO_HYZP_SEARCH_L",
    adjustDate: "",
    securityCode: ""
  },
  adjustmentParam: { //调整参数
    isPagination: false,
    sqlId: "SSE_ZQPZ_YSP_OPTZSXT_ADJUST_INFO_HYTZ_SEARCH_L",
    adjustDate: "",
    securityCode: ""
  },
  listSelectParam: { //下拉框代码参数
    isPagination: true,
    sqlId: "SSE_ZQPZ_YSP_SOPTZSXT_BASIC_INFO_YSHQ_BDZQDM_L"
  },
  init: function () {
    this.loadEvents();
    this.stockSelect();
    this.setStockParam();
  },
  loadEvents: function () {
    var _this = this;
    var radioHtm = '<label><i class="iconfont iconcircle iconcirclecheckfull"></i><input type="radio" name="codeName" value="全部">全部</label>';
    $(".js_code .sse_searchInput").html(radioHtm);
    //标的代码单选框选中
    $(".js_code").on("click", "input", function () {
      var radioVal = $(this).val();
      $(".js_code .iconcirclecheckfull").removeClass("iconcirclecheckfull");
      $(this).parent().find(".iconcircle").addClass("iconcirclecheckfull");
      _this.newlyLinkedParam.securityCode = radioVal && radioVal != "全部" ? radioVal : "";
      _this.delistParam.securityCode = radioVal && radioVal != "全部" ? radioVal : "";
      _this.adjustmentParam.securityCode = radioVal && radioVal != "全部" ? radioVal : "";
      _this.setStockParam();
    })
    $(".js_date input").attr("placeholder", "查询日期");
    //日期范围渲染
    laydate({
      elem: ".js_date input",
      theme: '#b50005',
      format: 'yyyy-MM-dd',
      min: '1990-12-19',
      trigger: 'click',
      btns: ['confirm'],
      done: function (value, date, endDate) {
        setTimeout(function () {
          _this.setStockParam();
        }, 500)
      },
    });
    var remarks = '<div class="remarks"><span>* 价格单位：元</span></div>';
    $(".js_newlyLinke").append(remarks);
    $(".js_delist").append(remarks);
    $(".js_adjustment").append(remarks);
  },
  setStockParam: function () {
    var _this = this;
    var dateStr = $(".js_date input").val();
    var txtTime = dateStr ? dateStr : _this.systemDate;
    $(".js_newlyLinke .new_date").text("更新日期：" + txtTime);
    $(".js_delist .new_date").text("更新日期：" + txtTime);
    $(".js_adjustment .new_date").text("更新日期：" + txtTime);
    _this.newlyLinkedParam.adjustDate = dateStr ? dateStr.replace(/-/g, "") : "";
    _this.delistParam.adjustDate = dateStr ? dateStr.replace(/-/g, "") : "";
    _this.adjustmentParam.adjustDate = dateStr ? dateStr.replace(/-/g, "") : "";
    //选择时间触发查询
    _this.getNewlyLinked();
    _this.getDelist();
    _this.getAdjustment();
  },
  //获取下拉框代码数据
  stockSelect: function () {
    var _this = this;
    getJSONP({
      url: _this.listingUrl,
      data: _this.listSelectParam,
      type: "post",
      successCallback: function (data) {
        if (data && data.result && data.result.length > 0) {
          var htm = '<label><i class="iconfont iconcircle iconcirclecheckfull"></i><input type="radio" name="codeName" checked value="全部">全部</label>';
          $.each(data.result, function (k, v) {
            htm += '<label><i class="iconfont iconcircle"></i><input type="radio" name="codeName" value="' + v.UNDERLYING_SECURITY_ID + '">' + v.UNDERLYING_SECURITY_ID + '</label>';
          })
          $(".js_code .sse_searchInput").html(htm);
        }
      }
    })
  },
  //新挂接口
  getNewlyLinked: function () {
    var _this = this;
    var htm = '<thead><tr>';
    htm += '<th class="text-nowrap">序号</th>';
    htm += '<th class="text-nowrap">挂牌日期</th>';
    htm += '<th>挂牌原因</th>';
    htm += '<th class="text-nowrap">合约编码</th>';
    htm += '<th class="text-nowrap">交易代码</th>';
    htm += '<th class="text-nowrap">合约简称</th>';
    htm += '<th>类型</th>';
    htm += '<th class="text-nowrap">到期月份</th>';
    htm += '<th class="text-nowrap text-right">行权价</th>';
    htm += '<th class="text-right">合约单位</th>';
    htm += '<th class="text-nowrap text-right">开盘参考价</th>';
    htm += '<th class="text-nowrap text-right">涨停价</th>';
    htm += '<th class="text-nowrap text-right">跌停价</th>';
    htm += '<th class="text-nowrap">行权日</th>';
    htm += '<th class="text-nowrap">最后交易日</th>';
    htm += '<th class="text-nowrap">到期日</th>';
    htm += '<th class="text-nowrap">交收日</th>';
    htm += '</tr></thead>';
    htm += '<tbody>';
    getJSONP({
      url: _this.listingUrl,
      data: _this.newlyLinkedParam,
      type: "post",
      successCallback: function (data) {
        if (_this.newlyLinkedParam.adjustDate == "") {
          $(".js_date input").val(_this.systemDate);
        }
        if (data && data.result && data.result.length > 0) {
          $.each(data.result, function (k, v) {
            if (k == 0) {
              var titleDate = v.ADJUST_DATE ? turnDateAddLine(v.ADJUST_DATE) : _this.systemDate;
              $(".js_newlyLinke .new_date").text("更新日期：" + titleDate);
              $(".js_date input").val(titleDate);
            }
            htm += '<tr>';
            htm += '<td class="text-nowrap">' + (k + 1) + '</td>'; //序号
            htm += '<td class="text-nowrap">' + v.ADJUST_DATE + '</td>'; //挂牌日期
            htm += '<td class="text-nowrap">' + v.UPDATE_TYPE + '</td>'; //挂牌原因
            htm += '<td class="text-nowrap">' + v.SECURITY_ID + '</td>'; //合约编码
            htm += '<td class="text-nowrap">' + v.CONTRACT_ID + '</td>'; //交易代码
            htm += '<td class="text-nowrap">' + v.CONTRACT_SYMBOL + '</td>'; //合约简称
            htm += '<td class="text-nowrap">' + v.CALL_OR_PUT + '</td>'; //类型
            htm += '<td class="text-nowrap">' + v.EXPIRE_MONTH + '</td>'; //到期月份
            htm += '<td class="text-nowrap text-right">' + v.EXERCISE_PRICE + '</td>'; //行权价
            htm += '<td class="text-nowrap text-right">' + v.CONTRACT_UNIT + '</td>'; //合约单位
            htm += '<td class="text-nowrap text-right">' + v.ADJUST_SETTLE_PRICE + '</td>'; //开盘参考价
            htm += '<td class="text-nowrap text-right">' + v.DAILY_PRICE_UPLIMIT + '</td>'; //涨停价
            htm += '<td class="text-nowrap text-right">' + v.DAILY_PRICE_DOWNLIMIT + '</td>'; //跌停价
            htm += '<td class="text-nowrap">' + v.EXERCISE_DATE + '</td>'; //行权日
            htm += '<td class="text-nowrap">' + v.END_DATE + '</td>'; //最后交易日
            htm += '<td class="text-nowrap">' + v.EXPIRE_DATE + '</td>'; //到期日
            htm += '<td class="text-nowrap">' + v.DELIVERY_DATE + '</td>'; //交收日
            htm += '</tr>';
          })
          htm += '</tbody>';
          $(".js_newlyLinke .table").html(htm);
        } else {
          htm += '<tr><td colspan="18">暂无数据</td></tr>';
          htm += '</tbody>';
          $(".js_newlyLinke .table").html(htm);
        }
      },
      errCallback: function () {
        htm += '<tr><td colspan="18">暂无数据</td></tr>';
        htm += '</tbody>';
        $(".js_newlyLinke .table").html(htm);
      }
    })
  },
  //摘牌接口
  getDelist: function () {
    var _this = this;
    var htm = '<thead><tr>';
    htm += '<th>序号</th>';
    htm += '<th>摘牌日期</th>';
    htm += '<th>摘牌原因</th>';
    htm += '<th>合约编码</th>';
    htm += '<th>交易代码</th>';
    htm += '<th>合约简称</th>';
    htm += '<th>类型</th>';
    htm += '<th>到期月份</th>';
    htm += '<th class="text-right">行权价</th>';
    htm += '<th class="text-right">合约单位</th>';
    htm += '<th>行权日</th>';
    htm += '<th>最后交易日</th>';
    htm += '<th>到期日</th>';
    htm += '<th>交收日</th>';
    htm += '</tr></thead>';
    htm += '<tbody>';
    getJSONP({
      url: _this.listingUrl,
      data: _this.delistParam,
      type: "post",
      successCallback: function (data) {
        if (data && data.result && data.result.length > 0) {
          $.each(data.result, function (k, v) {
            if (k == 0) {
              var titleDate = v.ADJUST_DATE ? turnDateAddLine(v.ADJUST_DATE) : _this.systemDate;
              $(".js_delist .new_date").text("更新日期：" + titleDate);
              $(".js_date input").val(titleDate);
            }
            htm += '<tr>';
            htm += '<td class="text-nowrap">' + (k + 1) + '</td>'; //序号
            htm += '<td class="text-nowrap">' + v.ADJUST_DATE + '</td>'; //摘牌日期
            htm += '<td class="text-nowrap">' + v.UPDATE_TYPE + '</td>'; //摘牌原因
            htm += '<td class="text-nowrap">' + v.SECURITY_ID + '</td>'; //合约编码
            htm += '<td class="text-nowrap">' + v.CONTRACT_ID + '</td>'; //交易代码
            htm += '<td class="text-nowrap">' + v.CONTRACT_SYMBOL + '</td>'; //合约简称
            htm += '<td class="text-nowrap">' + v.CALL_OR_PUT + '</td>'; //类型
            htm += '<td class="text-nowrap">' + v.EXPIRE_MONTH + '</td>'; //到期月份
            htm += '<td class="text-nowrap text-right">' + v.EXERCISE_PRICE + '</td>'; //行权价
            htm += '<td class="text-nowrap text-right">' + v.CONTRACT_UNIT + '</td>'; //合约单位
            htm += '<td class="text-nowrap">' + v.EXERCISE_DATE + '</td>'; //行权日
            htm += '<td class="text-nowrap">' + v.END_DATE + '</td>'; //最后交易日
            htm += '<td class="text-nowrap">' + v.EXPIRE_DATE + '</td>'; //到期日
            htm += '<td class="text-nowrap">' + v.DELIVERY_DATE + '</td>'; //交收日
            htm += '</tr>';
          })
          htm += '</tbody>';
          $(".js_delist .table").html(htm);
        } else {
          htm += '<tr><td colspan="18">暂无数据</td></tr>'
          htm += '</tbody>';
          $(".js_delist .table").html(htm);
        }
      },
      errCallback: function () {
        htm += '<tr><td colspan="18">暂无数据</td></tr>';
        htm += '</tbody>';
        $(".js_newlyLinke .table").html(htm);
      }
    })
  },
  //调整接口
  getAdjustment: function () {
    var _this = this;
    var htm = '<thead><tr>';
    htm += '<th>序号</th>';
    htm += '<th>调整原因</th>';
    htm += '<th>调整日期</th>';
    htm += '<th>合约编码</th>';
    htm += '<th>原交易代码</th>';
    htm += '<th>原合约简称</th>';
    htm += '<th class="text-right">原行权价</th>';
    htm += '<th class="text-right">原合约单位</th>';
    htm += '<th>新交易代码</th>';
    htm += '<th>新合约简称</th>';
    htm += '<th class="text-right">新行权价</th>';
    htm += '<th class="text-right">新合约单位</th>';
    htm += '<th>类型</th>';
    htm += '<th>到期月份</th>';
    htm += '<th>行权日</th>';
    htm += '<th>最后交易日</th>';
    htm += '<th>到期日</th>';
    htm += '<th>交收日</th>';
    htm += '<th>合约持仓</th>';
    htm += '</tr></thead>';
    htm += '<tbody>';
    getJSONP({
      url: _this.listingUrl,
      data: _this.adjustmentParam,
      type: "post",
      successCallback: function (data) {
        if (data && data.result && data.result.length > 0) {
          $.each(data.result, function (k, v) {
            if (k == 0) {
              var titleDate = v.ADJUST_DATE ? turnDateAddLine(v.ADJUST_DATE) : _this.systemDate;
              $(".js_adjustment .new_date").text("更新日期：" + titleDate);
              $(".js_date input").val(titleDate);
            }
            htm += '<tr>';
            htm += '<td class="text-nowrap">' + (k + 1) + '</td>'; //序号
            htm += '<td class="text-nowrap">' + v.UPDATE_TYPE + '</td>'; //调整原因
            htm += '<td class="text-nowrap">' + v.ADJUST_DATE + '</td>'; //调整日期
            htm += '<td class="text-nowrap">' + v.SECURITY_ID + '</td>'; //合约编码
            htm += '<td class="text-nowrap">' + v.ORG_CONTRACT_ID + '</td>'; //原交易代码
            htm += '<td class="text-nowrap">' + v.ORG_CONTRACT_SYMBOL + '</td>'; //原合约简称
            htm += '<td class="text-nowrap text-right">' + v.ORG_EXERCISE_PRICE + '</td>'; //原行权价
            htm += '<td class="text-nowrap text-right">' + v.ORG_CONTRACT_UNIT + '</td>'; //原合约单位
            htm += '<td class="text-nowrap">' + v.CONTRACT_ID + '</td>'; //新交易代码
            htm += '<td class="text-nowrap">' + v.CONTRACT_SYMBOL + '</td>'; //新合约简称
            htm += '<td class="text-nowrap text-right">' + v.EXERCISE_PRICE + '</td>'; //新行权价
            htm += '<td class="text-nowrap text-right">' + v.CONTRACT_UNIT + '</td>'; //新合约单位
            htm += '<td class="text-nowrap">' + v.CALL_OR_PUT + '</td>'; //类型
            htm += '<td class="text-nowrap">' + v.EXPIRE_MONTH + '</td>'; //到期月份
            htm += '<td class="text-nowrap">' + v.EXERCISE_DATE + '</td>'; //行权日
            htm += '<td class="text-nowrap">' + v.END_DATE + '</td>'; //最后交易日
            htm += '<td class="text-nowrap">' + v.EXPIRE_DATE + '</td>'; //到期日
            htm += '<td class="text-nowrap">' + v.DELIVERY_DATE + '</td>'; //交收日
            htm += '<td class="text-nowrap">' + v.LEAVES_QTY + '</td>'; //合约持仓
            htm += '</tr>';
          })
          htm += '</tbody>';
          $(".js_adjustment .table").html(htm);
        } else {
          htm += '<tr><td colspan="18">暂无数据</td></tr>';
          htm += '</tbody>';
          $(".js_adjustment .table").html(htm);
        }
      },
      errCallback: function () {
        htm += '<tr><td colspan="18">暂无数据</td></tr>';
        htm += '</tbody>';
        $(".js_newlyLinke .table").html(htm);
      }
    })
  }
}
if ($(".js_newlyLinke").length > 0) {
  stockListing.init();
}
/* 提醒信息 */
var reminderMessage = {
  systemDate: get_systemDate_global(),
  remindUrl: sseQueryURL + "commonQuery.do",
  maturityParam: { //不足5日参数
    "isPagination": true,
    "sqlId": "SSE_OPTZSXT_BASIC_INFO_TENDAYS_END_SEARCH_L",
    "securityCode": "",
    "refDate": "",
    "pageHelp.pageSize": 25,
    "pageHelp.pageNo": 1,
    "pageHelp.beginPage": 1,
    "pageHelp.endPage": 1,
    "pageHelp.cacheSize": 1,
  },
  recentParam: { //近期参数
    "isPagination": true,
    "sqlId": "SSE_OPTZSXT_BASIC_INFO_TENDAYS_ADJUST_SEARCH_L",
    "securityCode": "",
    "refDate": "",
    "pageHelp.pageSize": 25,
    "pageHelp.pageNo": 1,
    "pageHelp.beginPage": 1,
    "pageHelp.endPage": 1,
    "pageHelp.cacheSize": 1,
  },
  remindSelectParam: { //下拉框代码参数
    isPagination: true,
    sqlId: "SSE_ZQPZ_YSP_SOPTZSXT_BASIC_INFO_YSHQ_BDZQDM_L"
  },
  init: function () {
    this.loadEvents();
    this.stockSelect();
    this.setStockParam();
  },
  loadEvents: function () {
    var _this = this;
    //标的代码默认值
    var radioHtm = '<label><i class="iconfont iconcircle iconcirclecheckfull"></i><input type="radio" name="codeName" value="全部">全部</label>';
    $(".js_code .sse_searchInput").html(radioHtm);
    //标的代码单选框选中
    $(".js_code").on("click", "input", function () {
      var radioVal = $(this).val();
      $(".js_code .iconcirclecheckfull").removeClass("iconcirclecheckfull");
      $(this).parent().find(".iconcircle").addClass("iconcirclecheckfull");
      _this.maturityParam.securityCode = radioVal && radioVal != "全部" ? radioVal : "";
      _this.recentParam.securityCode = radioVal && radioVal != "全部" ? radioVal : "";
      _this.setStockParam();
    })
    $(".js_date input").attr("placeholder", "查询日期");
    //日期范围渲染
    laydate({
      elem: ".js_date input",
      theme: '#b50005',
      format: 'yyyy-MM-dd',
      trigger: 'click',
      btns: ['confirm'],
      done: function (value, date, endDate) {
        setTimeout(function () {
          _this.setStockParam();
        }, 500);
      },
    });
    var remarks = '<div class="remarks"><span>* 价格单位：元</span></div>';
    $(".js_maturity").append(remarks);
    $(".js_recent").append(remarks);
  },
  setStockParam: function () {
    var _this = this;
    var dateStr = $(".js_date input").val();
    var txtTime = dateStr ? dateStr : _this.systemDate; //初始化页面数据为空时更新时间是系统时间
    $(".js_date input").val(txtTime);
    $(".js_maturity .new_date").text("更新日期：" + txtTime);
    $(".js_recent .new_date").text("更新日期：" + txtTime);
    _this.maturityParam.refDate = dateStr.replace(/-/g, "");
    _this.recentParam.refDate = dateStr.replace(/-/g, "");
    _this.getMaturity(1);
    _this.getRecent(1);
  },
  //获取下拉框代码数据
  stockSelect: function () {
    var _this = this;
    getJSONP({
      url: _this.remindUrl,
      data: _this.remindSelectParam,
      type: "post",
      successCallback: function (data) {
        if (data && data.result && data.result.length > 0) {
          var htm = '<label><i class="iconfont iconcircle iconcirclecheckfull"></i><input type="radio" name="codeName" checked value="全部">全部</label>';
          $.each(data.result, function (k, v) {
            htm += '<label><i class="iconfont iconcircle"></i><input type="radio" name="codeName" value="' + v.UNDERLYING_SECURITY_ID + '">' + v.UNDERLYING_SECURITY_ID + '</label>';
          })
          $(".js_code .sse_searchInput").html(htm);
        }
      }
    })
  },
  //不足5日接口   
  getMaturity: function (pageIndex) {
    var _this = this;
    if (!paginationChange(_this.maturityParam, pageIndex, '.js_maturity')) { // 触发分页改变分页参数
      return
    }
    var htm = '<thead><tr>';
    htm += '<th class="text-nowrap">序号</th>';
    htm += '<th class="text-nowrap">合约标的</th>';
    htm += '<th class="text-nowrap">标的代码</th>';
    htm += '<th class="text-nowrap">合约编码</th>';
    htm += '<th class="text-nowrap">交易代码</th>';
    htm += '<th class="text-nowrap">合约简称</th>';
    htm += '<th class="text-nowrap">类型</th>';
    htm += '<th class="text-nowrap text-right">行权价</th>';
    htm += '<th class="text-nowrap text-right">合约单位</th>';
    htm += '<th class="text-nowrap">行权日</th>';
    htm += '<th class="text-nowrap">最后交易日</th>';
    htm += '<th class="text-nowrap">到期日</th>';
    htm += '<th class="text-nowrap">交收日</th>';
    htm += '<th class="text-nowrap text-right">合约持仓</th>';
    htm += '</tr></thead><tbody>';
    getJSONP({
      url: _this.remindUrl,
      data: _this.maturityParam,
      type: "post",
      successCallback: function (data) {
        if (data && data.result && data.result.length > 0) {
          $.each(data.result, function (k, v) {
            if (k == 0) {
              var titleDate = v.REF_DATE ? turnDateAddLine(v.REF_DATE) : _this.systemDate;
              $(".js_maturity .new_date").text("更新日期：" + titleDate);
              $(".js_recent .new_date").text("更新日期：" + titleDate);
              $(".js_date input").val(titleDate);
            }
            htm += '<tr>';
            htm += '<td class="text-nowrap">' + v.NUM + '</td>';
            htm += '<td class="text-nowrap">' + v.UNDERLYING_SYMBOL + '</td>';
            htm += '<td class="text-nowrap">' + v.UNDERLYING_SECURITY_ID + '</td>';
            htm += '<td class="text-nowrap">' + v.SECURITY_ID + '</td>';
            htm += '<td class="text-nowrap">' + v.CONTRACT_ID + '</td>';
            htm += '<td class="text-nowrap">' + v.CONTRACT_SYMBOL + '</td>';
            htm += '<td class="text-nowrap">' + v.CALL_OR_PUT + '</td>';
            htm += '<td class="text-nowrap text-right">' + v.EXERCISE_PRICE + '</td>';
            htm += '<td class="text-nowrap text-right">' + v.CONTRACT_UNIT + '</td>';
            htm += '<td class="text-nowrap">' + v.EXERCISE_DATE + '</td>';
            htm += '<td class="text-nowrap">' + v.END_DATE + '</td>';
            htm += '<td class="text-nowrap">' + v.EXPIRE_DATE + '</td>';
            htm += '<td class="text-nowrap">' + v.DELIVERY_DATE + '</td>';
            htm += '<td class="text-nowrap text-right">' + v.LEAVES_QTY + '</td>';
            htm += '</tr>';
          })
          htm += '</tbody>';
          $(".js_maturity .table").html(htm);
          Page.navigation(".js_maturity .pagination-box", data.pageHelp.pageCount, data.pageHelp.total, data.pageHelp.pageNo, data.pageHelp.pageSize, "reminderMessage.getMaturity");
          // $('.js_maturity .pagination-box .change-pageSize').remove();
          // $('.js_maturity').show();
        } else {
          htm += '<tr><td colspan="18">暂无数据</td></tr>'
          htm += '</tbody>';
          $(".js_maturity .table").html(htm);
          $(".js_maturity .pagination-box").html("");
        }
      },
      errCallback: function () {
        htm += '<tr><td colspan="18">暂无数据</td></tr>'
        htm += '</tbody>';
        $(".js_maturity .table").html(htm);
        $(".js_maturity .pagination-box").html("");
      }
    })
  },
  //近期接口
  getRecent: function (pageIndex) {
    var _this = this;
    if (!paginationChange(_this.recentParam, pageIndex, '.js_recent')) { // 触发分页改变分页参数
      return
    }
    var htm = '<thead><tr>';
    htm += '<th class="text-nowrap">序号</th>';
    htm += '<th class="text-nowrap">合约标的</th>';
    htm += '<th class="text-nowrap">标的代码</th>';
    htm += '<th class="text-nowrap">合约编码</th>';
    htm += '<th class="text-nowrap">交易代码</th>';
    htm += '<th class="text-nowrap">合约简称</th>';
    htm += '<th class="text-nowrap">类型</th>';
    htm += '<th class="text-nowrap text-right">行权价</th>';
    htm += '<th class="text-nowrap text-right">合约单位</th>';
    htm += '<th class="text-nowrap">行权日</th>';
    htm += '<th class="text-nowrap">最后交易日</th>';
    htm += '<th class="text-nowrap">到期日</th>';
    htm += '<th class="text-nowrap text-right">合约持仓</th>';
    htm += '</tr></thead><tbody>';
    getJSONP({
      url: _this.remindUrl,
      data: _this.recentParam,
      type: "post",
      successCallback: function (data) {
        if (data && data.result && data.result.length > 0) {
          $.each(data.result, function (k, v) {
            if (k == 0) {
              var titleDate = v.REF_DATE ? turnDateAddLine(v.REF_DATE) : _this.systemDate;
              $(".js_recent .new_date").text("更新日期：" + titleDate);
              $(".js_maturity .new_date").text("更新日期：" + titleDate);
              $(".js_date input").val(titleDate);
            }
            htm += '<tr>';
            htm += '<td class="text-nowrap">' + v.NUM + '</td>';
            htm += '<td class="text-nowrap">' + v.UNDERLYING_SYMBOL + '</td>';
            htm += '<td class="text-nowrap">' + v.UNDERLYING_SECURITY_ID + '</td>';
            htm += '<td class="text-nowrap">' + v.SECURITY_ID + '</td>';
            htm += '<td class="text-nowrap">' + v.CONTRACT_ID + '</td>';
            htm += '<td class="text-nowrap">' + v.CONTRACT_SYMBOL + '</td>';
            htm += '<td class="text-nowrap text-right">' + v.CALL_OR_PUT + '</td>';
            htm += '<td class="text-nowrap text-right">' + v.EXERCISE_PRICE + '</td>';
            htm += '<td class="text-nowrap">' + v.CONTRACT_UNIT + '</td>';
            htm += '<td class="text-nowrap">' + v.EXERCISE_DATE + '</td>';
            htm += '<td class="text-nowrap">' + v.END_DATE + '</td>';
            htm += '<td class="text-nowrap">' + v.EXPIRE_DATE + '</td>';
            htm += '<td class="text-nowrap text-right">' + v.LEAVES_QTY + '</td>';
            htm += '</tr>';
          })
          htm += '</tbody>';
          $(".js_recent .table").html(htm);
          Page.navigation(".js_recent .pagination-box", data.pageHelp.pageCount, data.pageHelp.total, data.pageHelp.pageNo, data.pageHelp.pageSize, "reminderMessage.getRecent");
          // $('.js_recent .pagination-box .change-pageSize').remove();
          // $('.js_recent').show();
        } else {
          htm += '<tr><td colspan="18">暂无数据</td></tr>';
          htm += '</tbody>';
          $(".js_recent .table").html(htm);
          $(".js_recent .pagination-box").html("");
        }
      },
      errCallback: function () {
        htm += '<tr><td colspan="18">暂无数据</td></tr>';
        htm += '</tbody>';
        $(".js_recent .table").html(htm);
        $(".js_recent .pagination-box").html("");
      }
    })
  }
}
if ($(".js_maturity").length > 0) {
  reminderMessage.init();
}
/* 交易信息 */
var transactionSituation = {
  transactionActiveUrl: sseQueryURL + "security/warrant/queryFormalOverviewActiveData.do", //交易情况
  transactionOverUrl: sseQueryURL + "security/warrant/queryFormalOverviewPositionData.do", //持仓情况
  transactionParam: {
    tradeDate: ""
  },
  init: function () {
    this.loadEvents();
    this.getTransAction();
    this.getTransOver();
  },
  loadEvents: function () {
    var _this = this;
    var totalHtm = '<span class="num_total"></span>';
    $(".js_transactionSituation .title_lev3").append(totalHtm);
    $(".js_positionSituation .title_lev3").append(totalHtm);

    $(".js_date input").attr("placeholder", "查询日期");
    //日期范围渲染
    laydate({
      elem: ".js_date input",
      theme: '#b50005',
      format: 'yyyy-MM-dd',
      min: '1990-12-19',
      trigger: 'click',
      btns: ['confirm'],
      done: function (value, date, endDate) {
        $(".js_transactionSituation .new_date").html("");
        $(".js_positionSituation .new_date").html("");
        $(".num_total").html("");
        _this.transactionParam.tradeDate = value.replace(/-/g, "");
        //选择时间触发查询
        _this.getTransAction();
        _this.getTransOver();
      },
    });
  },
  //最活跃3个合约品种交易情况
  getTransAction: function () {
    var _this = this;
    var totalTran = []; // 总交易量
    getJSONP({
      type: "post",
      url: _this.transactionActiveUrl,
      data: _this.transactionParam,
      successCallback: function (data) {
        if (data && data.result && data.result.length > 0) {
          var isHas = true;
          var htm = '<thead><tr><th class="back_gray border_transparent" colspan="5">认购</th><th class="back_gray" colspan="5">认沽</th></tr>';
          htm += '<tr>';
          htm += '<th class="text-nowrap border_transparent">标的券</th>';
          htm += '<th class="text-nowrap">总交易量（张）</th>';
          htm += '<th class="text-nowrap">名次</th>';
          htm += '<th class="text-nowrap">期权经营机构</th>';
          htm += '<th class="text-nowrap">交易量（张）</th>';
          htm += '<th class="text-nowrap border_transparent">标的券</th>';
          htm += '<th class="text-nowrap">总交易量（张）</th>';
          htm += '<th class="text-nowrap">名次</th>';
          htm += '<th class="text-nowrap">期权经营机构</th>';
          htm += '<th class="text-nowrap">交易量（张）</th>';
          htm += '</tr>';
          htm += '</thead><tbody>';
          $.each(data.result, function (k, v) {
            for (var j = 0; j < v.length; j++) {
              if (v[0].CALL_OR_PUT != null) {
                isHas = false;
                htm += '<tr>';
                if (j == 0) {
                  htm += '<td class="back_gray border_transparent" rowspan="5"><span class="table_typewrap">' + (v[j].SECURITY_VOLUME ? v[j].UNDERLYING_SYMBOL : "-") + '</span></td>'
                  htm += '<td class="text-nowrap table_borderRight" rowspan="5">' + (v[j].SECURITY_VOLUME ? v[j].SECURITY_VOLUME : "-") + '</td>'
                  totalTran.push(v[j].SECURITY_VOLUME)
                }
                htm += '<td class="text-nowrap">' + (v[j].MEMBER_RANK ? v[j].MEMBER_RANK : "-") + '</td>';
                htm += '<td class="text-nowrap">' + (v[j].MEMBER_ABBR ? v[j].MEMBER_ABBR : "-") + '</td>';
                htm += '<td class="text-nowrap">' + (v[j].MEMBER_VOLUME ? v[j].MEMBER_VOLUME : "-") + '</td>';
                j++;
                if (j == 1) {
                  htm += '<td class="back_gray border_transparent" rowspan="5"><span class="table_typewrap">' + (v[j].SECURITY_VOLUME ? v[j].UNDERLYING_SYMBOL : "-") + '</span></td>'
                  htm += '<td class="text-nowrap table_borderRight" rowspan="5">' + (v[j].SECURITY_VOLUME ? v[j].SECURITY_VOLUME : "-") + '</td>'
                  totalTran.push(v[j].SECURITY_VOLUME)
                }
                htm += '<td class="text-nowrap">' + (v[j].MEMBER_RANK ? v[j].MEMBER_RANK : "-") + '</td>';
                htm += '<td class="text-nowrap">' + (v[j].MEMBER_ABBR ? v[j].MEMBER_ABBR : "-") + '</td>';
                htm += '<td class="text-nowrap">' + (v[j].MEMBER_VOLUME ? v[j].MEMBER_VOLUME : "-") + '</td>';
                htm += '</tr>';
              }
            }
            if (k === data.result.length - 1 && !isHas) {
              $(".js_transactionSituation .num_total").text("统计总成交量最大的前3个合约品种");
            }
          })
          if (isHas) {
            htm += '<tr><td colspan="9">暂无数据</td></tr>';
          }
          htm += '</tbody>';
          $(".js_transactionSituation .table").html(htm);
          if (data.result[0][0].TRADE_DATE) {
            $(".js_transactionSituation .new_date").text("数据日期：" + data.result[0][0].TRADE_DATE);
            $(".js_date input").val(data.result[0][0].TRADE_DATE);
          }
        }
      }
    })
  },
  //持仓最大3个合约品种交易情况
  getTransOver: function () {
    var _this = this;
    var totalTran = []; // 总持仓量
    getJSONP({
      type: "post",
      url: _this.transactionOverUrl,
      data: _this.transactionParam,
      successCallback: function (data) {
        if (data && data.result && data.result.length > 0) {
          var isHas = true;
          var htm = '<thead><tr><th class="back_gray border_transparent" colspan="5">认购</th><th class="back_gray" colspan="5">认沽</th></tr>';
          htm += '<tr>';
          htm += '<th class="text-nowrap title_T02 border_transparent">标的券</th>';
          htm += '<th class="text-nowrap title_T02 ">总持仓量（张）</th>';
          htm += '<th class="text-nowrap title_T02">名次</th>';
          htm += '<th class="text-nowrap title_T02">期权经营机构</th>';
          htm += '<th class="text-nowrap title_T02">持仓量（张）</th>';
          htm += '<th class="text-nowrap title_T02 border_transparent">标的券</th>';
          htm += '<th class="text-nowrap title_T02">总持仓量（张）</th>';
          htm += '<th class="text-nowrap title_T02">名次</th>';
          htm += '<th class="text-nowrap title_T02">期权经营机构</th>';
          htm += '<th class="text-nowrap title_T02">持仓量（张）</th>';
          htm += '</tr>';
          htm += '</thead><tbody>';
          $.each(data.result, function (k, v) {
            for (var j = 0; j < v.length; j++) {
              if (v[0].CALL_OR_PUT != null) {
                isHas = false;
                htm += '<tr>';
                if (j == 0) {
                  htm += '<td class="back_gray border_transparent" rowspan="5"><span class="table_typewrap">' + (v[j].UNDERLYING_SYMBOL ? v[j].UNDERLYING_SYMBOL : "-") + '</span></td>'
                  htm += '<td class="text-nowrap table_borderRight" rowspan="5">' + (v[j].HOLD_QUANTITY ? v[j].HOLD_QUANTITY : "-") + '</td>'
                  totalTran.push(v[j].HOLD_QUANTITY)
                }
                htm += '<td class="text-nowrap">' + (v[j].MEMBER_RANK ? v[j].MEMBER_RANK : "-") + '</td>';
                htm += '<td class="text-nowrap">' + (v[j].MEMBER_ABBR ? v[j].MEMBER_ABBR : "-") + '</td>';
                htm += '<td class="text-nowrap">' + (v[j].MEMBER_HOLD ? v[j].MEMBER_HOLD : "-") + '</td>';
                j++;
                if (j == 1) {
                  htm += '<td class="back_gray border_transparent" rowspan="5"><span class="table_typewrap">' + (v[j].UNDERLYING_SYMBOL ? v[j].UNDERLYING_SYMBOL : "-") + '</span></td>'
                  htm += '<td class="text-nowrap table_borderRight" rowspan="5">' + (v[j].HOLD_QUANTITY ? v[j].HOLD_QUANTITY : "-") + '</td>'
                  totalTran.push(v[j].HOLD_QUANTITY)
                }
                htm += '<td class="text-nowrap">' + (v[j].MEMBER_RANK ? v[j].MEMBER_RANK : "-") + '</td>';
                htm += '<td class="text-nowrap">' + (v[j].MEMBER_ABBR ? v[j].MEMBER_ABBR : "-") + '</td>';
                htm += '<td class="text-nowrap">' + (v[j].MEMBER_HOLD ? v[j].MEMBER_HOLD : "-") + '</td>';
                htm += '</tr>';
              }
            }
            if (k === data.result.length - 1 && !isHas) {
              $(".js_positionSituation .num_total").text("统计总持仓量最大的前3个合约品种");
            }
          })
          if (isHas) {
            htm += '<tr><td colspan="9">暂无数据</td></tr>';
          }
          htm += '</tbody>';
          $(".js_positionSituation .table").html(htm);
          if (data.result[0][0].TRADE_DATE) {
            $(".js_positionSituation .new_date").text("数据日期：" + data.result[0][0].TRADE_DATE);
          }
        }
      }
    })
  },
  //计算总交易量
  toThousands: function (numArr) {
    var result = '',
      counter = 0,
      num = 0;
    for (var j = 0; j < numArr.length; j++) {
      if (numArr[j] == null) {
        numArr[j] = '0';
      }
      numArr[j] = Number(numArr[j].replace(/,/g, ''));
      num += numArr[j];
    }
    num = num.toString();
    for (var i = num.length - 1; i >= 0; i--) {
      counter++;
      result = num.charAt(i) + result;
      if (!(counter % 3) && i != 0) {
        result = ',' + result;
      }
    }
    return result;
  }
}
if ($(".js_transactionSituation").length > 0) {
  transactionSituation.init();
}
/* 行权交收信息 */
var exerciseTransaction = {
  systemDate: get_systemDate_global(),
  exerciseUrl: sseQueryURL + "commonQuery.do",
  exerciseParams: {
    "isPagination": false,
    "sqlId": "SSE_ZQPZ_YSP_GGQQZSXT_TJSJ_XQJGXX_SEARCH_L",
    "tradeDate": get_systemDate_global().replace(/-/g, ""),
  },
  init: function () {
    this.loadEvents();
    this.getExercise();
  },
  loadEvents: function () {
    var _this = this;
    $(".js_date input").attr("placeholder", "查询日期");

    //日期范围渲染
    laydate({
      elem: ".js_date input",
      theme: '#b50005',
      format: 'yyyy-MM-dd',
      value: _this.systemDate,
      trigger: 'click',
      btns: ['confirm'],
      done: function (value, date, endDate) {
        $(".js_exerciseTransaction .new_date").text(value);
        _this.exerciseParams.tradeDate = value.replace(/-/g, "");
        //选择时间触发查询
        _this.getExercise();
      },
    });
  },
  //行权交收接口
  getExercise: function () {
    var _this = this;
    var htm = '<thead><tr><th class="text-nowrap">日期</th><th class="text-nowrap">合约标的代码</th><th class="text-nowrap">合约标的名称</th><th class="text-nowrap text-right">认购期权行权数量</th><th class="text-nowrap text-right">认沽期权行权数量</th></tr></thead>';
    htm += '<tbody>';
    getJSONP({
      url: _this.exerciseUrl,
      data: _this.exerciseParams,
      type: "post",
      successCallback: function (data) {
        if (data && data.result && data.result.length > 0) {
          $.each(data.result, function (k, v) {
            htm += '<tr>';
            htm += '<td class="text-nowrap">' + v.TRADE_DATE + '</td>';
            htm += '<td class="text-nowrap">' + v.UNDERLYING_SECURITY_ID + '</td>';
            htm += '<td class="text-nowrap">' + v.UNDERLYING_SECURITY_ABBR + '</td>';
            htm += '<td class="text-nowrap text-right">' + v.CALL_EXE_VALUE + '</td>';
            htm += '<td class="text-nowrap text-right">' + v.PUT_EXE_VALUE + '</td>';
            htm += '</tr>';
          })
          htm += '</tbody>';
          $(".js_exerciseTransaction .table").html(htm);
        } else {
          htm += '<tr><td colspan="18">暂无数据</td></tr>';
          htm += '</tbody>';
          $(".js_exerciseTransaction .table").html(htm);
        }
      },
      errCallback: function () {
        htm += '<tr><td colspan="18">暂无数据</td></tr>';
        htm += '</tbody>';
        $(".js_exerciseTransaction .table").html(htm);
      }
    })
  }
}
if ($(".js_exerciseTransaction").length > 0) {
  exerciseTransaction.init();
}

/* 市场参与人 */
var participantslisting = {
  //市场参与人参数
  participantsParams: {
    isPagination: true,
    "pageHelp.pageSize": 25,
    "pageHelp.pageNo": 1,
    "pageHelp.beginPage": 1,
    "pageHelp.cacheSize": 1,
    "pageHelp.endPage": 1,
    'sqlId': 'COMMON_SSE_SJ_HYTJSJ_ZXYWZG_L',
    'PERMISSION_TYPE': 'option'
  },
  //市场参与人url
  participantsUrl: sseQueryURL + "commonQuery.do?jsonCallBack=?",
  init: function () {
    this.loadEvents();
  },
  loadEvents: function () {
    var _this = this;
    $('.js_marketpt .title_lev2').html('上海证券交易所股票期权经纪业务参与人');
    //初始加载
    _this.getParticipantsrList(1);
  },
  //调用接口
  getParticipantsrList: function (pageIndex) {
    var _this = this;
    if (!paginationChange(_this.participantsParams, pageIndex)) {
      //触发分页改变分页参数
      return;
    }
    var participantsHtml =
      "<thead><tr><th class='text-nowrap'>编号</th><th class='text-nowrap'>参与人名称（按首字母排序）</th></tr></thead>";
    var emptyTr = '<tr><td colspan="2">暂无数据</td></tr>';
    getJSONP({
      type: 'post',
      dataType: 'jsonp',
      url: _this.participantsUrl,
      data: _this.participantsParams,
      successCallback: function (data) {
        if (data && data.result && data.result.length > 0) {
          $.each(data.result, function (k, v) {
            participantsHtml += "<tr>";
            participantsHtml +=
              '<td class="text-nowrap">' + v.NUM + '</td>'; //序号
            participantsHtml += '<td class="text-nowrap">' + v.FULL_NAME + '</td>'; //公司名称
            participantsHtml += "</tr>";
          });
          participantsHtml += "</tbody>";
          $(".js_marketpt .table").html(participantsHtml);
          //调用分页
          Page.navigation(
            ".js_marketpt .pagination-box",
            data.pageHelp.pageCount,
            data.pageHelp.total,
            data.pageHelp.pageNo,
            data.pageHelp.pageSize,
            "participantslisting.getParticipantsrList"
          );
        } else {
          participantsHtml += emptyTr;
          participantsHtml += "</tbody>";
          $(".js_marketpt .table").html(participantsHtml);
          $(".js_marketpt .pagination-box").html("");
        }
      },
      errCallback: function () {
        participantsHtml += emptyTr;
        participantsHtml += "</tbody>";
        $(".js_marketpt .table").html(participantsHtml);
        $(".js_marketpt .pagination-box").html("");
      },
    });
  },
};
if ($(".js_marketpt").length > 0) {
  participantslisting.init();
}
