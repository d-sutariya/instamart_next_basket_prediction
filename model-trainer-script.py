class ModelTrainer:
    
    def __init__(self,experiment_name,dask_client,train_set,test_set=None):
        self.train_set = train_set
        self.test_set = test_set
        self.exp_name = experiment_name
        self.client = dask_client
        mlflow.set_experiment(self.exp_name)

    def __log_details(self,y_true,preds,prev_commit_hash,params,model=None):
        #log params 
        if params!=None:
                mlflow.log_params(params)
        else:
                mlflow.log_param("params",None)
                
        if self.test_set is not None:  
            
            #log metrics 
            pred_logits = [1 if pred >= 0.5 else 0 for pred in preds]
            mlflow.log_metric("precision",precision_score(y_true,pred_logits))
            mlflow.log_metric("recall",recall_score(y_true,pred_logits))
            mlflow.log_metric("f1",f1_score(y_true,pred_logits))
            mlflow.log_metric("AUC",roc_auc_score(y_true,preds))
            mlflow.log_metric("logloss",log_loss(y_true,preds))
            
        # log script url with version 
        commit_url = "https://github.com/d-sutariya/instacart_next_basket_prediction/tree/" + prev_commit_hash
        mlflow.log_param("repository url",commit_url)

        # log environment 
        os.system("conda env export > conda.yaml")
        mlflow.log_artifact("conda.yaml")

        # log dataset 
        dataset_path = "https://www.kaggle.com/datasets/deepsutariya/instacart-exp-data" 
        mlflow.log_param("dataset url",dataset_path)

        if model != None:
            
            explainer = shap.TreeExplainer(model)
            shap_values = explainer.shap_values(self.test_set.drop("reordered",axis=1).compute())
            shap.summary_plot(shap_values,self.test_set.drop(["reordered"],axis=1).compute(),show=False)
            plt.savefig('shap_summary_plot.png',bbox_inches='tight')
            plt.close()
            mlflow.log_artifact('shap_summary_plot.png')
            
        mlflow.end_run()
    
    def train_h2o_glm(self,prev_commit_hash,params=None):
        
        h2o_logistic_model = H2OGeneralizedLinearEstimator(family='binomial') \
                            .train(x=self.train_set.drop("reordered").columns,y='reordered',training_frame=self.train_set)
        # log the important stuffs
        with mlflow.start_run():
            
            mlflow.h2o.log_model(h2o_logistic_model,"h2o_logistic_model")
            
            # log perameters
            mlflow.log_param("family","binomial")
            mlflow.log_param("alpha",h2o_logistic_model.get_params()['alpha'])
            mlflow.log_param("lambda",h2o_logistic_model.get_params()['lambda'])

            preds = h2o_logistic_model.predict(self.test_set).as_data_frame()['p1']
            y_true = self.test_set['reordered'].as_data_frame()['reordered']

            # log dataset path,script path, environment
            self.__log_details(y_true,preds,prev_commit_hash,params)         
            
            del y_true ,preds 
            gc.collect()
        
        return h2o_logistic_model

    def train_h2o_gbm(self,prev_commit_hash,params=None):

        if params != None:
            if "distribution" not in params.keys():
                params['distribution'] = 'bernoulli'
            
            start = time.time()
                
            h2o_gbm = H2OGradientBoostingEstimator(**params) \
                    .train(x=self.train_set.drop("reordered").columns,y='reordered',training_frame = self.train_set)
        else:
            
            start = time.time()
            h2o_gbm = H2OGradientBoostingEstimator() \
                    .train(x=self.train_set.drop("reordered").columns,y='reordered',training_frame = self.train_set)
            
        duration = get_time(start)
        
        with mlflow.start_run():
            # log model
            mlflow.h2o.log_model(h2o_gbm,"h2o_gbm_model")
            
            # log params
            if params!=None:
                mlflow.log_params(params)
            else:
                mlflow.log_param("params",None)
                
            mlflow.log_param("training_time",duration)
            
            preds = h2o_gbm.predict(self.test_set).as_data_frame()['p1']
            y_true = self.test_set['reordered'].as_data_frame()['reordered']

            # log evaluation metrics , dataset path,script path, environment
            self.__log_details(y_true,preds,prev_commit_hash,params,h2o_gbm) 

        del y_true,preds
        gc.collect()
        return h2o_gbm
    
    def train_xgb_rf(self,prev_commit_hash,params=None):
        
        dtrain = dxgb.DaskDMatrix(self.client,self.train_set.drop("reordered",axis=1), label = self.train_set['reordered'])
        dtest = dxgb.DaskDMatrix(self.client,self.test_set.drop("reordered",axis=1), label = self.test_set['reordered'])
        
        if params != None:
            
            if 'booster' not in params.keys():
                params['booster'] = 'gbtree'

            if 'device' not in params.keys():
                params['device'] = 'cuda'
                
            if 'tree_method' not in params.keys():
                params['tree_method'] = 'hist'
                
            start = time.time()
            xgb_rf = dxgb.train(self.client,params = params,dtrain=dtrain,num_boost_round=1)

        else :
            params = {
                'booster':'gbtree',
                'objective':'binary:logistic',
                'device':'cuda',
                'tree_method':'hist'
            }
            
            start = time.time()
            xgb_rf = dxgb.train(self.client,params = params,dtrain=dtrain,num_boost_round=1)
            
        duration = get_time(start)
        
        with mlflow.start_run():

            mlflow.xgboost.log_model(xgb_rf['booster'],"xgb_rf_model")
            
            mlflow.log_param("training_time",duration)

            preds = dxgb.predict(self.client,xgb_rf, dtest)
            y_true = self.test_set['reordered']

            self.__log_details(y_true,preds,prev_commit_hash,params,xgb_rf['booster'])
       
        del y_true,preds,dtrain,dtest
        gc.collect()
        return xgb_rf

    def train_xgb_gbm(self,prev_commit_hash,params=None):
        
        dtrain = dxgb.DaskDMatrix(self.client,self.train_set.drop("reordered",axis=1), label = self.train_set['reordered'])
        dtest = dxgb.DaskDMatrix(self.client,self.test_set.drop("reordered",axis=1), label = self.test_set['reordered'])
        
        if params != None:
            
            if 'booster' not in params.keys():
                params['booster'] = 'gbtree'

            if 'device' not in params.keys():
                params['device'] = 'cuda'

            if 'tree_method' not in params.keys():
                params['tree_method'] = 'hist'
        else :
            params = {
                'booster':'gbtree',
                'objective':'binary:logistic',
                'device':'cuda',
                'tree_method':'hist'
            }
        
        start = time.time()
        xgb_gbm = dxgb.train(self.client,
                             params = params,
                            dtrain=dtrain,
                            evals = [(dtest,'eval')],
                            early_stopping_rounds=20,
                            num_boost_round = 100
        )   
        duration = get_time(start)

        with mlflow.start_run():

            mlflow.xgboost.log_model(xgb_gbm['booster'],"xgb_gbm_model")
            
            mlflow.log_param("training_time",duration)

            preds = dxgb.predict(self.client,xgb_gbm, dtest)
            y_true = self.test_set['reordered']

            self.__log_details(y_true,preds,prev_commit_hash,params,xgb_gbm['booster'])
            
        del y_true,preds,dtrain,dtest
        gc.collect()
        return xgb_gbm

    def train_lgb_gbm(self,prev_commit_hash,params=None):
        # dtrain = lgb.DaskDataset(self.train_set.drop("reordered",axis=1),label=self.train_set['reordered'])
        # dtest = lgb.DaskDataset(self.test_set.drop("reordered",axis=1),label=self.test_set['reordered'],reference = dtrain)

        if params != None:
            
            # if  'verbose' not in params.keys():
            #     params['verbose'] = -1
                
            if 'objective'  not in params.keys():
                params['objective'] = 'binary'
            
            if 'device' not in params.keys():
                params['device'] = 'gpu'
                
        else:
            
            params = {
                # 'verbose':-1,
                'objective':'binary',
                'device':'gpu'
            }
            
        start = time.time()
        lgb_gbm = lgb.DaskLGBMClassifier(**params, n_estimators=100)
        
        lgb_gbm.fit(
                    self.train_set.drop("reordered",axis=1),self.train_set["reordered"], 
                  eval_set=[(self.test_set.drop("reordered",axis=1),self.test_set["reordered"])], 
                  callbacks=[lgb.early_stopping(stopping_rounds=20)]
                   )

            
        duration = get_time(start)

        with mlflow.start_run():

            mlflow.lightgbm.log_model(lgb_gbm,"lgb_gbm")
            
            mlflow.log_param("training_time",duration)
            preds = lgb_gbm.predict(self.test_set.drop("reordered",axis=1)).compute()
            y_true = self.test_set['reordered']
            self.__log_details(y_true,preds,prev_commit_hash,params,lgb_gbm)
            
        del y_true,preds
        gc.collect()
        return lgb_gbm